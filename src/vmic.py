#!/usr/bin/env python3
"""Virtual microphone CLI: real mic -> Hush denoise -> BlackHole virtual device.

Captures your real microphone, removes background human voices and noise with the
Hush (Weya NC) model, and pumps the cleaned audio into the BlackHole virtual
device. Any app (Zoom, Meet, Teams, Discord, QuickTime) can then select
"BlackHole 2ch" as its microphone.

This is a thin CLI over ``engine.VMicEngine`` (shared with the menu-bar app).

Usage::

    python3 src/vmic.py                          # default mic + BlackHole, aggressive
    python3 src/vmic.py --input-device "MacBook Pro"   # raw hardware mic
    python3 src/vmic.py --atten-lim-db 40        # gentler suppression
    python3 src/vmic.py --passthrough            # bypass Hush (A/B test routing)
    python3 src/vmic.py --list-devices

Press Ctrl+C to stop. First run triggers a macOS microphone-permission prompt
for your terminal — allow it.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import VMicEngine  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Hush virtual microphone.")
    parser.add_argument("--input-device", default=None,
                        help="Mic device index or name substring (default: system mic)")
    parser.add_argument("--output-device", default=None,
                        help="Output device (default: first 'BlackHole')")
    parser.add_argument("--samplerate", type=int, default=48000)
    parser.add_argument("--atten-lim-db", type=float, default=20.0,
                        help="Max attenuation in dB (default 20 = gentle)")
    parser.add_argument("--passthrough", action="store_true",
                        help="Bypass Hush; route mic straight through (A/B test)")
    parser.add_argument("--list-devices", action="store_true")
    args = parser.parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return

    try:
        eng = VMicEngine(
            input_match=args.input_device,
            output_match=args.output_device,
            samplerate=args.samplerate,
            mode="off" if args.passthrough else "gentle",
            atten_override=None if args.passthrough else args.atten_lim_db,
        )
    except OSError as exc:
        raise SystemExit(
            f"Failed to load sounddevice/PortAudio: {exc}\n"
            "Install PortAudio: `brew install portaudio`, then `pip install sounddevice`."
        )

    try:
        eng.start()
    except LookupError as exc:
        raise SystemExit(str(exc))

    mode = "PASSTHROUGH (no denoise)" if args.passthrough else "DENOISING"
    print(f"Hush virtual microphone — {mode}")
    print(f"  Input : [{eng.in_dev}] {eng.in_name}")
    print(f"  Output: [{eng.out_dev}] {eng.out_name}  ({eng.out_channels} ch)")
    print(f"  Rate  : {args.samplerate} Hz | frame {eng.frame_len} samples "
          f"({eng.frame_len / args.samplerate * 1000:.0f} ms) | "
          f"atten {args.atten_lim_db} dB")
    print(f"  -> In your call app, select '{eng.out_name}' as the microphone.")
    print("  Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1.0)
            s = eng.stats
            print(f"  in/out {s['in']}/{s['out']}  drops {s['drop']}  "
                  f"underruns {s['under']}  SNR {s['snr']:+.1f} dB   ",
                  end="\r", flush=True)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        eng.stop()


if __name__ == "__main__":
    main()
