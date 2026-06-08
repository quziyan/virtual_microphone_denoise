#!/usr/bin/env python3
"""Prove the virtual mic's BlackHole output really goes through Hush.

Runs the SAME engine that the menu-bar app uses, pointing mic -> BlackHole, and
captures what lands on BlackHole's input (= what Zoom would hear) in two modes:

  off         : passthrough (model bypassed)
  aggressive  : Hush active (100 dB)

If 'aggressive' is markedly quieter/different than 'off' on the same ambient,
the model is demonstrably in the BlackHole path.

Stop the menu-bar app first (it holds the mic). Then:
    .venv/bin/python tests/verify_vmic_path.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from engine import VMicEngine, find_device  # noqa: E402

SR = 48000


def capture_blackhole(seconds: float, dev: int) -> np.ndarray:
    n = int(seconds * SR)
    rec = sd.rec(n, samplerate=SR, channels=2, dtype="float32", device=dev)
    sd.wait()
    return rec.mean(axis=1)


def rms(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(a ** 2)) + 1e-12)


def main() -> None:
    bh_in = find_device(sd, "BlackHole", "input")
    print(f"Capturing from [{bh_in}] {sd.query_devices(bh_in)['name']}")
    print("Speak/keep ambient steady for ~7s total...\n")

    out = {}
    for mode in ["off", "aggressive"]:
        eng = VMicEngine(input_match="MacBook Pro", output_match="BlackHole", mode=mode)
        eng.start()
        time.sleep(0.6)  # let the pipeline fill and output start
        sig = capture_blackhole(3.0, bh_in)
        eng.stop()
        time.sleep(0.3)
        out[mode] = rms(sig)
        print(f"  mode={mode:<11} BlackHole RMS = {out[mode]:.5f}")

    delta = 20 * np.log10(out["aggressive"] / out["off"])
    print(f"\n  aggressive vs off: {delta:+.1f} dB on the BlackHole output")
    if delta < -3.0:
        print("  RESULT: Hush IS active on the virtual-mic path "
              "(aggressive output is attenuated vs passthrough). ✅")
    else:
        print("  RESULT: little difference — either the room was silent in both, "
              "or the model is not engaging. Re-run while talking/with noise.")


if __name__ == "__main__":
    main()
