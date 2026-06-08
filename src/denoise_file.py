#!/usr/bin/env python3
"""File-in -> file-out denoising using the Hush (Weya NC) model.

This is the offline proof-of-concept that validates the model pipeline before
going live on the virtual microphone. It reads a WAV file, runs every frame
through Hush (which removes background speakers and noise), and writes a cleaned
WAV file at the same sample rate.

Usage::

    python3 src/denoise_file.py --input assets/sample_00006_raw.wav \
                                --output /tmp/cleaned.wav

The Weya NC library resamples internally, so any input sample rate works: the
rate you pass is the rate frames are exchanged at, and the output matches it.
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

# Make the vendored wrapper importable regardless of where we're launched from
# (repo checkout in dev, sys._MEIPASS inside a frozen .app bundle).
if getattr(sys, "frozen", False):
    REPO_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR = REPO_ROOT / "vendor"
sys.path.insert(0, str(VENDOR))

from weya_nc import WeyaNC  # noqa: E402  (import after sys.path tweak)

LIB_PATH = VENDOR / "lib" / "libweya_nc.dylib"
MODEL_PATH = VENDOR / "models" / "advanced_dfnet16k_model_best_onnx.tar.gz"


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    """Load a WAV as float32 mono in [-1, 1], returning (samples, sample_rate)."""
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sw != 2:
        raise ValueError(f"Expected 16-bit PCM WAV, got sample width {sw} bytes")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch == 2:
        audio = audio.reshape(-1, 2).mean(axis=1)
    elif ch != 1:
        raise ValueError(f"Expected mono or stereo WAV, got {ch} channels")
    return audio, sr


def save_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    """Write float32 mono [-1, 1] audio to a 16-bit mono WAV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (audio * 32768.0).clip(-32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def denoise(audio: np.ndarray, sr: int, atten_lim_db: float) -> np.ndarray:
    """Run audio through Hush frame-by-frame and return the cleaned signal."""
    with WeyaNC(
        lib_path=str(LIB_PATH),
        model_path=str(MODEL_PATH),
        sample_rate=sr,
        atten_lim_db=atten_lim_db,
    ) as nc:
        frame_len = nc.frame_length
        n = len(audio)
        # Pad up to a whole number of frames.
        pad = (-n) % frame_len
        if pad:
            audio = np.concatenate([audio, np.zeros(pad, dtype=np.float32)])
        out = np.empty_like(audio)
        for i in range(0, len(audio), frame_len):
            out[i : i + frame_len] = nc.process_frame(audio[i : i + frame_len])
        return out[:n]


def main() -> None:
    parser = argparse.ArgumentParser(description="Denoise a WAV file with Hush.")
    parser.add_argument(
        "--input", default=str(REPO_ROOT / "assets" / "sample_00006_raw.wav")
    )
    parser.add_argument("--output", default="/tmp/hush_cleaned.wav")
    parser.add_argument(
        "--atten-lim-db",
        type=float,
        default=100.0,
        help="Max attenuation in dB (100 = aggressive, lower = gentler)",
    )
    args = parser.parse_args()

    in_path = Path(args.input).resolve()
    out_path = Path(args.output).resolve()

    audio, sr = load_wav(in_path)
    print(f"Loaded {in_path.name}: {len(audio)} samples @ {sr} Hz "
          f"({len(audio) / sr:.2f}s)")

    cleaned = denoise(audio, sr, args.atten_lim_db)
    save_wav(out_path, cleaned, sr)

    # Report how much the signal changed — a sanity signal that Hush ran.
    in_rms = float(np.sqrt(np.mean(audio ** 2)) + 1e-12)
    out_rms = float(np.sqrt(np.mean(cleaned ** 2)) + 1e-12)
    delta = float(np.sqrt(np.mean((audio - cleaned) ** 2)))
    print(f"Saved cleaned audio -> {out_path}")
    print(f"  input RMS:  {in_rms:.4f}")
    print(f"  output RMS: {out_rms:.4f}  ({20 * np.log10(out_rms / in_rms):+.1f} dB)")
    print(f"  change RMS: {delta:.4f}  (0 would mean nothing happened)")


if __name__ == "__main__":
    main()
