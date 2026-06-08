"""Core for the 'noise-reduction tuning' feature (UI-agnostic).

Records a short clip from the physical mic, then renders that same clip at every
suppression level so the user can A/B them and pick the best. Used by the native
Settings window; kept separate from any UI so it can be reused.
"""

from __future__ import annotations

import wave
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


def record_and_process(seconds: float, input_match, borrow_mic) -> list[dict]:
    """Record `seconds` from the mic and process at every level.

    input_match() -> the physical-mic device spec.
    borrow_mic(acquire, token) -> stop the engine to free the mic / restart after.
    Returns [{key, label, atten, path, reduction_db}, ...]. Blocks ~`seconds`, so
    call off the UI thread.
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
        _save_wav(path, audio, SR)
        out.append({
            "key": key,
            "label": label,
            "atten": atten,
            "path": str(path),
            "reduction_db": round(20 * float(np.log10(_rms(audio) / raw_rms)), 1),
        })
    return out
