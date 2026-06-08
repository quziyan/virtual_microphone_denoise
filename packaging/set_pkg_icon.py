#!/usr/bin/env python3
"""Set a custom Finder icon on a file (used for the .pkg installer).

A .pkg can't embed an icon the way an .app does; the icon is a Finder-level
attribute set via NSWorkspace. Uses pyobjc (already present via rumps).

Usage:  python3 packaging/set_pkg_icon.py packaging/AppIcon.png dist/Foo.pkg
"""

import sys

from AppKit import NSImage, NSWorkspace

image_path, target = sys.argv[1], sys.argv[2]
img = NSImage.alloc().initWithContentsOfFile_(image_path)
if img is None:
    print(f"could not load image: {image_path}")
    sys.exit(1)
ok = NSWorkspace.sharedWorkspace().setIcon_forFile_options_(img, target, 0)
print(f"set icon on {target}: {ok}")
sys.exit(0 if ok else 1)
