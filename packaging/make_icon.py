#!/usr/bin/env python3
"""Turn a square PNG (possibly on a white canvas) into a macOS AppIcon.icns.

Steps: trim the near-white border → pad to square → resize to 1024 → apply a
macOS-style rounded-rectangle alpha mask (so the corners are transparent, not a
white box) → emit an .iconset at all required sizes → iconutil → .icns.
Also writes AppIcon.png (1024, masked) for setting the .pkg's Finder icon.

Usage:  python3 packaging/make_icon.py packaging/icon-source.png packaging
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SRC = Path(sys.argv[1])
OUT = Path(sys.argv[2])
CORNER_RATIO = 0.225  # Apple squircle is ~22-23% of the side

img = Image.open(SRC).convert("RGBA")

# 1. Trim near-white border to the artwork's bounding box.
arr = np.array(img)
nonwhite = np.any(arr[:, :, :3] < 245, axis=2)
ys, xs = np.where(nonwhite)
if len(xs):
    img = img.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))

# 2. Pad to a centered transparent square.
w, h = img.size
side = max(w, h)
canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
canvas.paste(img, ((side - w) // 2, (side - h) // 2))

# 3. Resize to 1024 and apply rounded-corner alpha (corners -> transparent).
base = canvas.resize((1024, 1024), Image.LANCZOS)
mask = Image.new("L", (1024, 1024), 0)
ImageDraw.Draw(mask).rounded_rectangle(
    [0, 0, 1024, 1024], radius=int(1024 * CORNER_RATIO), fill=255
)
base.putalpha(mask)
base.save(OUT / "AppIcon.png")

# 4. Build the .iconset and convert to .icns.
iconset = OUT / "AppIcon.iconset"
iconset.mkdir(exist_ok=True)
for sz in (16, 32, 128, 256, 512):
    base.resize((sz, sz), Image.LANCZOS).save(iconset / f"icon_{sz}x{sz}.png")
    base.resize((sz * 2, sz * 2), Image.LANCZOS).save(iconset / f"icon_{sz}x{sz}@2x.png")

subprocess.run(
    ["iconutil", "-c", "icns", "-o", str(OUT / "AppIcon.icns"), str(iconset)],
    check=True,
)
print(f"wrote {OUT / 'AppIcon.icns'} and {OUT / 'AppIcon.png'}")
