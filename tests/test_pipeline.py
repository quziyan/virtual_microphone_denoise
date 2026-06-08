#!/usr/bin/env python3
"""Offline regression test for the Hush denoising pipeline.

Runs the bundled sample WAV through Hush and asserts:
  1. The library + model load and process without error.
  2. The output is valid audio (finite, in range, right length).
  3. The output actually changed (Hush did something).
  4. The output matches upstream's reference denoised file at the documented
     160-sample algorithmic delay (correlation ~1.0) — proving correctness.

Run::  .venv/bin/python tests/test_pipeline.py
Exit code 0 = pass.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from denoise_file import denoise, load_wav  # noqa: E402

RAW = REPO_ROOT / "assets" / "sample_00006_raw.wav"
REF = REPO_ROOT / "assets" / "sample_00006_denoised.wav"
DELAY = 160  # fft_size - hop_size, documented algorithmic delay


def _load_i16(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(
            np.float32
        ) / 32768.0


def main() -> int:
    audio, sr = load_wav(RAW)
    assert sr == 16000, f"expected 16 kHz sample, got {sr}"

    cleaned = denoise(audio, sr, atten_lim_db=100.0)

    # 2. valid audio
    assert cleaned.shape == audio.shape, "output length mismatch"
    assert np.all(np.isfinite(cleaned)), "output has NaN/Inf"
    assert np.max(np.abs(cleaned)) <= 1.0 + 1e-6, "output out of [-1, 1]"

    # 3. changed
    change = float(np.sqrt(np.mean((audio - cleaned) ** 2)))
    assert change > 1e-3, f"output ~identical to input (change={change:.2e})"

    # 4. matches upstream reference at the documented delay
    ref = _load_i16(REF)
    n = min(len(cleaned), len(ref)) - DELAY
    corr = float(np.corrcoef(cleaned[DELAY : DELAY + n], ref[:n])[0, 1])
    assert corr > 0.99, f"correlation with reference too low: {corr:.4f}"

    print(f"PASS  change_rms={change:.4f}  ref_corr@{DELAY}={corr:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
