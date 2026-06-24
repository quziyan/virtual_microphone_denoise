#!/usr/bin/env python
"""Windows executable entry for VibeCodingVirMic.

The macOS product uses BlackHole/CoreAudio plus a macOS-only ``libweya_nc.dylib``.
On Windows, Python cannot create a microphone device by itself; users need a
virtual cable driver such as VB-CABLE or VoiceMeeter. This executable provides
the Windows-side routing process:

    physical mic -> optional Weya NC DLL denoise -> virtual cable playback device

The target voice app should select the matching virtual cable recording device
as its microphone, for example ``CABLE Output`` when routing to ``CABLE Input``.
If ``vendor/lib/weya_nc.dll`` is not present, the route still works in
passthrough mode and prints the limitation clearly.
"""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time
from pathlib import Path

import numpy as np

APP_NAME = "VibeCodingVirMic Windows"
DEFAULT_SAMPLERATE = 48000
DEFAULT_FRAME_MS = 10
VIRTUAL_OUTPUT_HINTS = (
    "cable input",
    "vb-audio",
    "voicemeeter input",
    "voicemeeter aux input",
    "voicemeeter vaio",
)
VIRTUAL_INPUT_HINTS = (
    "cable output",
    "voicemeeter output",
    "voicemeeter aux output",
)

if getattr(sys, "frozen", False):
    BASE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    BASE = Path(__file__).resolve().parent.parent
VENDOR = BASE / "vendor"
sys.path.insert(0, str(VENDOR))

MODEL_PATH = VENDOR / "models" / "advanced_dfnet16k_model_best_onnx.tar.gz"
WINDOWS_DLL = VENDOR / "lib" / "weya_nc.dll"


def _matches_any(name: str, hints: tuple[str, ...]) -> bool:
    lower = name.lower()
    return any(hint in lower for hint in hints)


def _device_name(sd, index: int) -> str:
    return str(sd.query_devices(index)["name"])


def list_devices(sd) -> None:
    default_in, default_out = sd.default.device
    print("Audio devices:")
    for idx, dev in enumerate(sd.query_devices()):
        tags: list[str] = []
        if idx == default_in:
            tags.append("default input")
        if idx == default_out:
            tags.append("default output")
        if dev["max_input_channels"] > 0:
            tags.append(f"in:{dev['max_input_channels']}")
        if dev["max_output_channels"] > 0:
            tags.append(f"out:{dev['max_output_channels']}")
        print(f"  [{idx:2d}] {dev['name']}  ({', '.join(tags)})")


def find_device(sd, spec, kind: str, *, allow_virtual_input: bool = False):
    chan_key = "max_input_channels" if kind == "input" else "max_output_channels"
    if spec is None:
        return None
    if isinstance(spec, int) or (isinstance(spec, str) and spec.isdigit()):
        idx = int(spec)
        dev = sd.query_devices(idx)
        if dev[chan_key] <= 0:
            raise LookupError(f"Device [{idx}] has no {kind} channels.")
        return idx
    needle = str(spec).lower()
    for idx, dev in enumerate(sd.query_devices()):
        name = str(dev["name"])
        if needle in name.lower() and dev[chan_key] > 0:
            if kind == "input" and not allow_virtual_input and _matches_any(name, VIRTUAL_INPUT_HINTS):
                continue
            return idx
    raise LookupError(f"No {kind} device matching '{spec}'.")


def default_physical_input(sd):
    default_in = sd.default.device[0]
    if default_in is not None and default_in >= 0:
        name = _device_name(sd, default_in)
        if not _matches_any(name, VIRTUAL_INPUT_HINTS):
            return default_in
    for idx, dev in enumerate(sd.query_devices()):
        name = str(dev["name"])
        if dev["max_input_channels"] > 0 and not _matches_any(name, VIRTUAL_INPUT_HINTS):
            return idx
    return None


def default_virtual_output(sd):
    for idx, dev in enumerate(sd.query_devices()):
        name = str(dev["name"])
        if dev["max_output_channels"] > 0 and _matches_any(name, VIRTUAL_OUTPUT_HINTS):
            return idx
    return None


def optional_weya_session(sample_rate: int, atten_lim_db: float):
    if not WINDOWS_DLL.exists():
        return None, f"missing {WINDOWS_DLL.name}"
    if not MODEL_PATH.exists():
        return None, f"missing {MODEL_PATH.name}"
    try:
        from weya_nc import WeyaNC

        session = WeyaNC(
            lib_path=str(WINDOWS_DLL),
            model_path=str(MODEL_PATH),
            sample_rate=sample_rate,
            atten_lim_db=atten_lim_db,
        )
        return session, "available"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


class AudioRouter:
    def __init__(
        self,
        sd,
        input_device: int,
        output_device: int,
        samplerate: int,
        atten_lim_db: float,
        passthrough: bool,
    ) -> None:
        self.sd = sd
        self.input_device = input_device
        self.output_device = output_device
        self.samplerate = samplerate
        self.atten_lim_db = atten_lim_db
        self.passthrough = passthrough
        self.lock = threading.Lock()
        self.fifo: queue.Queue[np.ndarray] = queue.Queue(maxsize=64)
        self.stats = {"in": 0, "out": 0, "drop": 0, "under": 0, "snr": 0.0}
        self._snr: list[float] = []
        self.nc = None
        self.nc_status = "disabled by --passthrough" if passthrough else ""
        self.in_stream = None
        self.out_stream = None
        self.out_channels = 1
        self.frame_len = max(1, int(samplerate * DEFAULT_FRAME_MS / 1000))

    def start(self) -> None:
        if not self.passthrough:
            self.nc, self.nc_status = optional_weya_session(self.samplerate, self.atten_lim_db)
            if self.nc is not None:
                self.frame_len = int(self.nc.frame_length)
        self.out_channels = max(1, int(self.sd.query_devices(self.output_device)["max_output_channels"]))
        self.in_stream = self.sd.InputStream(
            device=self.input_device,
            samplerate=self.samplerate,
            blocksize=self.frame_len,
            channels=1,
            dtype="float32",
            callback=self._input_callback,
        )
        self.out_stream = self.sd.OutputStream(
            device=self.output_device,
            samplerate=self.samplerate,
            blocksize=self.frame_len,
            channels=self.out_channels,
            dtype="float32",
            callback=self._output_callback,
        )
        self.out_stream.start()
        self.in_stream.start()

    def stop(self) -> None:
        for stream in (self.in_stream, self.out_stream):
            if stream is None:
                continue
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        self.in_stream = None
        self.out_stream = None
        if self.nc is not None:
            try:
                self.nc.close()
            finally:
                self.nc = None

    def _input_callback(self, indata, frames, time_info, status) -> None:
        if status:
            print(f"[input] {status}", file=sys.stderr)
        frame_in = indata[:, 0].copy()
        if self.nc is None:
            frame_out = frame_in
        else:
            frame_out = self.nc.process_frame(frame_in)
            sig = float(np.mean(frame_out**2))
            noise = float(np.mean((frame_in - frame_out) ** 2))
            if noise > 1e-10:
                self._snr.append(10 * np.log10(sig / noise + 1e-10))
                if len(self._snr) >= 50:
                    self.stats["snr"] = float(np.mean(self._snr))
                    self._snr.clear()
        self.stats["in"] += 1
        try:
            self.fifo.put_nowait(frame_out)
        except queue.Full:
            self.stats["drop"] += 1

    def _output_callback(self, outdata, frames, time_info, status) -> None:
        if status:
            print(f"[output] {status}", file=sys.stderr)
        try:
            frame = self.fifo.get_nowait()
        except queue.Empty:
            self.stats["under"] += 1
            outdata[:, :] = 0
            return
        self.stats["out"] += 1
        outdata[:, :] = frame.reshape(-1, 1)


def print_usage_notes(output_name: str) -> None:
    print("\nWindows usage:")
    print("  1. Install a virtual audio cable, for example VB-CABLE or VoiceMeeter.")
    print(f"  2. This router writes to: {output_name}")
    print("  3. In the target voice/meeting app, select the matching recording device")
    print("     as the microphone, usually 'CABLE Output' for VB-CABLE.")
    print("  4. Do not select the virtual cable output as this app's input, or it loops.")


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME}: mic to Windows virtual cable router.")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit.")
    parser.add_argument("--selftest", action="store_true", help="Check imports and Windows runtime packaging.")
    parser.add_argument("--input-device", help="Input device index or name substring. Default: physical default input.")
    parser.add_argument("--output-device", help="Output device index or name substring. Default: first virtual cable output.")
    parser.add_argument("--samplerate", type=int, default=DEFAULT_SAMPLERATE)
    parser.add_argument("--atten-lim-db", type=float, default=20.0)
    parser.add_argument("--passthrough", action="store_true", help="Force passthrough; no denoise session.")
    parser.add_argument("--allow-non-virtual-output", action="store_true", help="Allow default speaker output if no virtual cable is found.")
    args = parser.parse_args()

    try:
        import sounddevice as sd
    except Exception as exc:
        print(f"sounddevice/PortAudio failed to load: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.list_devices:
        list_devices(sd)
        return 0

    if args.selftest:
        print(f"{APP_NAME} selftest")
        print(f"  Python frozen: {bool(getattr(sys, 'frozen', False))}")
        print(f"  Base: {BASE}")
        print(f"  sounddevice: ok")
        print(f"  Windows Weya DLL: {'present' if WINDOWS_DLL.exists() else 'missing'} ({WINDOWS_DLL})")
        print(f"  Model bundle: {'present' if MODEL_PATH.exists() else 'missing'} ({MODEL_PATH})")
        print("  Note: denoise on Windows requires vendor/lib/weya_nc.dll.")
        return 0

    try:
        in_dev = find_device(sd, args.input_device, "input") if args.input_device else default_physical_input(sd)
        if in_dev is None:
            raise LookupError("No physical input device found. Use --list-devices and pass --input-device.")

        out_dev = find_device(sd, args.output_device, "output") if args.output_device else default_virtual_output(sd)
        if out_dev is None and args.allow_non_virtual_output:
            default_out = sd.default.device[1]
            if default_out is not None and default_out >= 0:
                out_dev = default_out
        if out_dev is None:
            raise LookupError(
                "No virtual cable output device found. Install VB-CABLE/VoiceMeeter, "
                "or pass --output-device, or use --allow-non-virtual-output for testing."
            )
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        print("\nRun with --list-devices to inspect available devices.", file=sys.stderr)
        return 3

    router = AudioRouter(
        sd=sd,
        input_device=in_dev,
        output_device=out_dev,
        samplerate=args.samplerate,
        atten_lim_db=args.atten_lim_db,
        passthrough=args.passthrough,
    )
    input_name = _device_name(sd, in_dev)
    output_name = _device_name(sd, out_dev)

    try:
        router.start()
    except Exception as exc:
        print(f"Failed to start audio route: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4

    denoise_state = "passthrough"
    if router.nc is not None:
        denoise_state = f"denoise {args.atten_lim_db:g} dB"
    elif not args.passthrough:
        denoise_state = f"passthrough ({router.nc_status})"

    print(f"{APP_NAME} started")
    print(f"  Input : [{in_dev}] {input_name}")
    print(f"  Output: [{out_dev}] {output_name}")
    print(f"  Mode  : {denoise_state}")
    print(f"  Rate  : {args.samplerate} Hz | frame {router.frame_len} samples")
    print_usage_notes(output_name)
    print("\nPress Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
            s = router.stats
            print(
                f"  in/out {s['in']}/{s['out']}  drops {s['drop']}  underruns {s['under']}  SNR {s['snr']:+.1f} dB",
                end="\r",
                flush=True,
            )
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        router.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
