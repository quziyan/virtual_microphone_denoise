#!/usr/bin/env python3
"""End-to-end check: record what reaches BlackHole and confirm audio is flowing.

Run this WHILE the virtual mic is running (menu-bar app Started, or src/vmic.py).
BlackHole is a loopback, so its *input* carries exactly what the app sends to its
*output* — i.e. what Zoom/Meet would receive. We record that for a few seconds
while you talk, then report the level and save a WAV you can listen to.

    .venv/bin/python tests/test_e2e_blackhole.py --seconds 6 --out /tmp/blackhole_capture.wav
"""

from __future__ import annotations

import argparse
import sys
import time
import wave

import numpy as np
import sounddevice as sd


def find_blackhole_input() -> int:
    for i, d in enumerate(sd.query_devices()):
        if "blackhole" in d["name"].lower() and d["max_input_channels"] > 0:
            return i
    raise SystemExit("BlackHole input device not found.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--out", default="/tmp/blackhole_capture.wav")
    ap.add_argument("--samplerate", type=int, default=48000)
    args = ap.parse_args()

    dev = find_blackhole_input()
    name = sd.query_devices(dev)["name"]
    sr = args.samplerate
    print(f"Recording from [{dev}] {name} for {args.seconds:.0f}s @ {sr} Hz")
    print(">>> TALK NOW (and play some background voices to test removal) <<<")

    n = int(args.seconds * sr)
    rec = sd.rec(n, samplerate=sr, channels=2, dtype="float32", device=dev)
    for s in range(int(args.seconds), 0, -1):
        print(f"  ...{s}", end="\r", flush=True)
        time.sleep(1.0)
    sd.wait()
    print("\nDone recording.")

    mono = rec.mean(axis=1)
    rms = float(np.sqrt(np.mean(mono ** 2)) + 1e-12)
    peak = float(np.max(np.abs(mono)))
    dbfs = 20 * np.log10(rms + 1e-12)

    # Save for listening.
    pcm = (mono * 32768.0).clip(-32768, 32767).astype(np.int16)
    with wave.open(args.out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())

    print(f"  RMS level: {rms:.4f}  ({dbfs:+.1f} dBFS) | peak {peak:.3f}")
    print(f"  Saved: {args.out}")
    if rms < 1e-4:
        print("  RESULT: SILENT — no audio reached BlackHole. Is the app Started "
              "and routing to BlackHole? Is mic permission granted?")
        sys.exit(1)
    print("  RESULT: AUDIO IS FLOWING to BlackHole. End-to-end path works. "
          "Open the WAV to hear the cleaned signal.")


if __name__ == "__main__":
    main()
