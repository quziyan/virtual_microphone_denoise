"""Core for the 'noise-reduction tuning' feature (UI-agnostic).

Records a short clip from the physical mic, then renders that same clip at every
suppression level so the user can A/B them and pick the best. Also renders a Mel
spectrogram PNG for each level. Used by the native Settings window.

No librosa / PIL at runtime — the Mel spectrogram is computed with numpy and the
PNG is written with stdlib zlib, so it works inside the frozen .app bundle.
"""

from __future__ import annotations

import struct
import wave
import zlib
from pathlib import Path

import numpy as np

# (key, label, atten_db | None = raw passthrough). Keys match menubar MODES.
LEVELS = [
    ("off", "原始 Raw (0 dB)", None),
    ("gentle", "Gentle (20 dB)", 20.0),
    ("db40", "Medium (40 dB)", 40.0),
    ("db60", "Strong (60 dB)", 60.0),
    ("aggressive", "Aggressive (100 dB)", 100.0),
]
SR = 48000
TUNE_DIR = Path.home() / "Library" / "Application Support" / "VibeCodingVirMic" / "tuning"


def _save_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    pcm = (audio * 32768.0).clip(-32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _rms(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(a ** 2)) + 1e-9) if len(a) else 1e-9


# --- Mel spectrogram (numpy) + PNG (stdlib) --------------------------------

def _hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + f / 700.0)


def _mel_to_hz(m):
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def _mel_filterbank(sr, n_fft, n_mels, fmax=None):
    if fmax is None:
        fmax = sr / 2.0
    n_bins = n_fft // 2 + 1
    pts = _mel_to_hz(np.linspace(_hz_to_mel(0.0), _hz_to_mel(fmax), n_mels + 2))
    bins = np.clip(np.floor((n_fft + 1) * pts / sr).astype(int), 0, n_bins - 1)
    fb = np.zeros((n_mels, n_bins), dtype=np.float32)
    for m in range(1, n_mels + 1):
        l, c, r = bins[m - 1], bins[m], bins[m + 1]
        if c > l:
            fb[m - 1, l:c] = (np.arange(l, c) - l) / (c - l)
        if r > c:
            fb[m - 1, c:r] = (r - np.arange(c, r)) / (r - c)
    return fb


def _mel_spectrogram(audio, sr, n_fft=1024, hop=512, n_mels=64):
    win = np.hanning(n_fft)
    audio = np.asarray(audio, dtype=np.float64)                # float64: no overflow
    if len(audio) < n_fft:
        audio = np.pad(audio, (0, n_fft - len(audio)))
    cols = [np.abs(np.fft.rfft(audio[s:s + n_fft] * win)) ** 2
            for s in range(0, len(audio) - n_fft + 1, hop)]
    if not cols:
        cols = [np.zeros(n_fft // 2 + 1)]
    power = np.asarray(cols, dtype=np.float64).T               # (bins, frames)
    # Apple's Accelerate BLAS emits spurious over/divide warnings on arm64 even
    # though the result is finite — silence them; values are validated below.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        mel = _mel_filterbank(sr, n_fft, n_mels).astype(np.float64) @ power
    return 10.0 * np.log10(np.maximum(mel, 1e-10))


# magma-ish colour anchors (perceptual, dark→bright).
_CMAP = np.array([
    [0, 0, 4], [40, 11, 84], [101, 21, 110], [159, 42, 99],
    [212, 72, 66], [245, 125, 21], [250, 193, 39], [252, 255, 164],
], dtype=np.float32) / 255.0


def _colormap(norm):
    n = len(_CMAP) - 1
    x = np.clip(norm, 0.0, 1.0) * n
    i = np.clip(np.floor(x).astype(int), 0, n - 1)
    f = (x - i)[..., None]
    rgb = _CMAP[i] * (1.0 - f) + _CMAP[i + 1] * f
    return (rgb * 255.0).astype(np.uint8)


def _write_png(path, rgb):
    """Write an H×W×3 uint8 array as a PNG using only stdlib zlib."""
    h, w = rgb.shape[:2]
    flat = np.ascontiguousarray(rgb, dtype=np.uint8).reshape(h, w * 3)
    raw = bytearray()
    for y in range(h):
        raw.append(0)               # filter type 0 (none)
        raw.extend(flat[y].tobytes())

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
        fh.write(chunk(b"IEND", b""))


def write_mel_png(audio, sr, out_path) -> None:
    mel = _mel_spectrogram(audio, sr)
    mn, mx = float(mel.min()), float(mel.max())
    norm = (mel - mn) / (mx - mn + 1e-9)
    norm = np.flipud(norm)                  # low frequency at the bottom
    _write_png(out_path, _colormap(norm))


# --- record + process ------------------------------------------------------

def record_and_process(seconds: float, input_match, borrow_mic) -> list[dict]:
    """Record `seconds` from the mic and process at every level.

    Returns [{key, label, atten, path, mel_path, reduction_db}, ...]. Blocks
    ~`seconds`, so call off the UI thread.
    """
    import sounddevice as sd
    from denoise_file import denoise
    from engine import resolve_device

    TUNE_DIR.mkdir(parents=True, exist_ok=True)
    token = borrow_mic(True, None)
    try:
        dev = resolve_device(sd, input_match(), "input", None)
        raw = sd.rec(int(seconds * SR), samplerate=SR, channels=1,
                     dtype="float32", device=dev)
        sd.wait()
        raw = raw[:, 0]
    finally:
        borrow_mic(False, token)

    raw_rms = _rms(raw)
    out: list[dict] = []
    for key, label, atten in LEVELS:
        audio = raw if atten is None else denoise(raw, SR, atten)
        path = TUNE_DIR / f"{key}.wav"
        mel_path = TUNE_DIR / f"{key}_mel.png"
        _save_wav(path, audio, SR)
        try:
            write_mel_png(audio, SR, str(mel_path))
            mp = str(mel_path)
        except Exception:
            mp = None
        out.append({
            "key": key,
            "label": label,
            "atten": atten,
            "path": str(path),
            "mel_path": mp,
            "reduction_db": round(20 * float(np.log10(_rms(audio) / raw_rms)), 1),
        })
    return out
