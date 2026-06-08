#!/usr/bin/env python3
"""Reusable virtual-microphone engine: real mic -> Hush denoise -> output device.

Wraps the capture/denoise/playback pipeline in a controllable object so both the
CLI (`vmic.py`) and the menu-bar app (`menubar.py`) drive the same code.

    eng = VMicEngine(input_match="MacBook Pro")
    eng.start()                 # opens streams, begins denoising
    eng.set_mode("gentle")      # off | gentle | aggressive  (live)
    eng.stop()

Changing to a different suppression strength rebuilds the Hush session (atten is
fixed at session creation), which causes a ~10 ms blip. Toggling "off"
(passthrough) is instant — it just bypasses the model, no rebuild.
"""

from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path

import numpy as np

# Resource base differs between dev (repo checkout) and a frozen .app bundle
# (PyInstaller unpacks data under sys._MEIPASS).
if getattr(sys, "frozen", False):
    BASE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    BASE = Path(__file__).resolve().parent.parent
VENDOR = BASE / "vendor"
sys.path.insert(0, str(VENDOR))  # dev mode; harmless when frozen

from weya_nc import WeyaNC  # noqa: E402

LIB_PATH = VENDOR / "lib" / "libweya_nc.dylib"
MODEL_PATH = VENDOR / "models" / "advanced_dfnet16k_model_best_onnx.tar.gz"

DEFAULT_SAMPLERATE = 48000
DEFAULT_OUTPUT_MATCH = "BlackHole"

# Suppression presets in dB. "off" means passthrough (model bypassed).
MODE_ATTEN = {"gentle": 20.0, "db40": 40.0, "db60": 60.0, "aggressive": 100.0}


def find_device(sd, match: str, kind: str) -> int:
    """Find an audio device index by case-insensitive name substring."""
    match_l = match.lower()
    chan_key = "max_input_channels" if kind == "input" else "max_output_channels"
    for idx, dev in enumerate(sd.query_devices()):
        if match_l in dev["name"].lower() and dev[chan_key] > 0:
            return idx
    raise LookupError(
        f"No {kind} device matching '{match}'. "
        f"(For BlackHole: `brew install blackhole-2ch`.)"
    )


def resolve_device(sd, spec, kind: str, default_match):
    """Resolve a device spec (index, name substring, or None) to an index."""
    if spec is None:
        if default_match is not None:
            return find_device(sd, default_match, kind)
        default = sd.default.device[0 if kind == "input" else 1]
        if default is None or default < 0:
            raise LookupError(f"No default {kind} device available.")
        return default
    if isinstance(spec, int) or (isinstance(spec, str) and spec.isdigit()):
        return int(spec)
    return find_device(sd, spec, kind)


def list_input_devices(sd, exclude_substr: str = "blackhole") -> list[tuple[int, str]]:
    """All input-capable devices, excluding the loopback target (BlackHole).

    Deduplicated by name (CoreAudio sometimes reports duplicates). Returns
    [(index, name), ...] — these are the physical mics the user can pick.
    """
    out: list[tuple[int, str]] = []
    seen: set[str] = set()
    for idx, dev in enumerate(sd.query_devices()):
        name = dev["name"]
        if dev["max_input_channels"] > 0 and exclude_substr not in name.lower():
            if name not in seen:
                out.append((idx, name))
                seen.add(name)
    return out


def default_input_match(sd):
    """A safe default physical mic: the system default input unless it's the
    loopback device (which would feed our own output back in), else the first
    real input. Returns a device index, or None if there are no inputs.
    """
    try:
        di = sd.default.device[0]
        if di is not None and di >= 0:
            name = sd.query_devices(di)["name"]
            if "blackhole" not in name.lower():
                return di
    except Exception:
        pass
    devs = list_input_devices(sd)
    return devs[0][0] if devs else None


class VMicEngine:
    """Controllable mic -> Hush -> output-device pipeline.

    Parameters mirror the CLI flags. ``input_match`` / ``output_match`` accept a
    device index, a name substring, or None (system default mic / first BlackHole).
    """

    def __init__(
        self,
        input_match=None,
        output_match=None,
        samplerate: int = DEFAULT_SAMPLERATE,
        mode: str = "aggressive",
        atten_override: float | None = None,
        jitter_frames: int = 4,
    ) -> None:
        import sounddevice as sd  # imported here so import errors surface to caller

        self._sd = sd
        self._input_match = input_match
        self._output_match = output_match
        self._samplerate = samplerate
        self._jitter_frames = jitter_frames

        self._lock = threading.Lock()
        self._nc: WeyaNC | None = None
        self._in_stream = None
        self._out_stream = None
        self._fifo: queue.Queue | None = None

        self._mode = mode if mode in ("off", *MODE_ATTEN) else "aggressive"
        # When set (CLI --atten-lim-db), overrides the preset dB for non-off modes.
        self._atten_override = atten_override
        self._session_atten: float | None = None  # atten the live session was built with

        # Live stats (read by UIs).
        self.stats = {"in": 0, "out": 0, "drop": 0, "under": 0, "snr": 0.0}
        self._snr_accum: list[float] = []

        self.in_dev = None
        self.out_dev = None
        self.in_name = ""
        self.out_name = ""
        self.out_channels = 2
        self.frame_len = 0

    # -- public state --------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._in_stream is not None

    @property
    def mode(self) -> str:
        return self._mode

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Resolve devices, build the Hush session, and open both streams."""
        with self._lock:
            if self.running:
                return
            sd = self._sd
            self.in_dev = resolve_device(sd, self._input_match, "input", None)
            self.out_dev = resolve_device(
                sd, self._output_match, "output", DEFAULT_OUTPUT_MATCH
            )
            self.in_name = sd.query_devices(self.in_dev)["name"]
            out_info = sd.query_devices(self.out_dev)
            self.out_name = out_info["name"]
            self.out_channels = max(1, out_info["max_output_channels"])

            self._build_session_locked(self._current_atten())
            self.frame_len = self._nc.frame_length if self._nc else self._frame_len_passthrough()

            self._fifo = queue.Queue(maxsize=64)
            self._silence = np.zeros(self.frame_len, dtype=np.float32)

            self._out_stream = sd.OutputStream(
                device=self.out_dev, samplerate=self._samplerate,
                blocksize=self.frame_len, channels=self.out_channels,
                dtype="float32", callback=self._out_cb,
            )
            self._in_stream = sd.InputStream(
                device=self.in_dev, samplerate=self._samplerate,
                blocksize=self.frame_len, channels=1,
                dtype="float32", callback=self._in_cb,
            )
            self._out_stream.start()
            self._in_stream.start()

    def stop(self) -> None:
        """Stop both streams and free the Hush session."""
        with self._lock:
            for s in (self._in_stream, self._out_stream):
                if s is not None:
                    # A yanked device makes stop()/close() raise — ignore so we
                    # can still tear down and recover onto another mic.
                    try:
                        s.stop()
                    except Exception:
                        pass
                    try:
                        s.close()
                    except Exception:
                        pass
            self._in_stream = None
            self._out_stream = None
            if self._nc is not None:
                self._nc.close()
                self._nc = None
            self._session_atten = None
            self._fifo = None

    def toggle(self) -> bool:
        """Start if stopped, stop if running. Returns the new running state."""
        if self.running:
            self.stop()
        else:
            self.start()
        return self.running

    def set_input(self, spec) -> None:
        """Switch the physical input device live (index or name substring).

        If running, the streams are rebuilt so the new mic takes effect at once.
        """
        self._input_match = spec
        if self.running:
            self.stop()
            self.start()

    def set_mode(self, mode: str) -> None:
        """Switch suppression mode live: 'off' | 'gentle' | 'aggressive'."""
        if mode not in ("off", *MODE_ATTEN):
            raise ValueError(f"unknown mode: {mode}")
        with self._lock:
            self._mode = mode
            self._atten_override = None  # presets win once a mode is picked in the UI
            if not self.running:
                return
            target = self._current_atten()
            # Passthrough (off) needs no session; non-off needs a session at the
            # right atten. Rebuild only when the needed atten differs.
            if target is None:
                return  # _in_cb checks self._mode each frame; nothing to rebuild
            if self._session_atten != target:
                self._build_session_locked(target)

    # -- internals -----------------------------------------------------------

    def _current_atten(self):
        if self._mode == "off":
            return None
        if self._atten_override is not None:
            return self._atten_override
        return MODE_ATTEN[self._mode]

    def _frame_len_passthrough(self) -> int:
        # When starting in 'off' mode we still need a frame size; derive it from
        # a throwaway session at 16 kHz-equivalent for this sample rate.
        probe = WeyaNC(lib_path=str(LIB_PATH), model_path=str(MODEL_PATH),
                       sample_rate=self._samplerate, atten_lim_db=100.0)
        n = probe.frame_length
        probe.close()
        return n

    def _build_session_locked(self, atten):
        """(Re)create the Hush session at the given atten. Caller holds the lock."""
        if self._nc is not None:
            self._nc.close()
            self._nc = None
        if atten is None:
            return  # passthrough: no model session
        self._nc = WeyaNC(
            lib_path=str(LIB_PATH), model_path=str(MODEL_PATH),
            sample_rate=self._samplerate, atten_lim_db=atten,
        )
        self._session_atten = atten

    def _in_cb(self, indata, frames, time_info, status):
        if status:
            print(f"[in] {status}", file=sys.stderr)
        frame_in = indata[:, 0].copy()
        nc = self._nc
        if self._mode == "off" or nc is None:
            frame_out = frame_in
        else:
            frame_out = nc.process_frame(frame_in)
            sig = float(np.mean(frame_out ** 2))
            noise = float(np.mean((frame_in - frame_out) ** 2))
            if noise > 1e-10:
                self._snr_accum.append(10 * np.log10(sig / noise + 1e-10))
                if len(self._snr_accum) >= 50:
                    self.stats["snr"] = float(np.mean(self._snr_accum))
                    self._snr_accum.clear()
        self.stats["in"] += 1
        if self._fifo is not None:
            try:
                self._fifo.put_nowait(frame_out)
            except queue.Full:
                self.stats["drop"] += 1

    def _out_cb(self, outdata, frames, time_info, status):
        if status:
            print(f"[out] {status}", file=sys.stderr)
        frame = None
        if self._fifo is not None:
            try:
                frame = self._fifo.get_nowait()
                self.stats["out"] += 1
            except queue.Empty:
                frame = None
        if frame is None:
            self.stats["under"] += 1
            outdata[:, :] = 0.0
            return
        outdata[:, :] = frame.reshape(-1, 1)
