#!/usr/bin/env bash
# Build the single-file .pkg installer: VibeCodingVirMic.app + BlackHole driver.
# Prereq: the .app is already built+signed at dist/VibeCodingVirMic.app
#   (run packaging/build_app.sh first).
set -euo pipefail
cd "$(dirname "$0")/.."

# Version comes from the single source of truth: src/version.py.
VER="$(.venv/bin/python -c 'import sys; sys.path.insert(0, "src"); from version import __version__; print(__version__)')"
[ -n "$VER" ] || { echo "ERROR: could not read version from src/version.py"; exit 1; }
APP="dist/VibeCodingVirMic.app"
STAGE="build/pkg"
SRC_DRV="/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver"

[ -d "$APP" ] || { echo "ERROR: $APP not found — build the app first."; exit 1; }
[ -d "$SRC_DRV" ] || { echo "ERROR: BlackHole driver not found at $SRC_DRV (brew install blackhole-2ch)."; exit 1; }

echo "==> Staging payload..."
rm -rf "$STAGE"
mkdir -p "$STAGE/root/Applications" "$STAGE/scripts"
cp -R "$APP" "$STAGE/root/Applications/"

echo "==> Bundling BlackHole driver..."
tar -C "$(dirname "$SRC_DRV")" -cf "$STAGE/scripts/BlackHole2ch.driver.tar" "$(basename "$SRC_DRV")"
cp packaging/postinstall "$STAGE/scripts/postinstall"
chmod +x "$STAGE/scripts/postinstall"

echo "==> Generating component plist and DISABLING relocation..."
# pkgbuild auto-marks .app bundles as relocatable, which makes the installer
# place the app wherever an existing copy is registered (LaunchServices) instead
# of /Applications. Force BundleIsRelocatable=false so it always lands in
# /Applications (and turn off version-checking so an old receipt can't skip it).
pkgbuild --analyze --root "$STAGE/root" "build/Component.plist"
.venv/bin/python - "build/Component.plist" <<'PY'
import plistlib, sys
path = sys.argv[1]
with open(path, "rb") as f:
    comps = plistlib.load(f)
for c in comps:
    c["BundleIsRelocatable"] = False
    c["BundleIsVersionChecked"] = False
with open(path, "wb") as f:
    plistlib.dump(comps, f)
print(f"  patched {len(comps)} component(s): relocation OFF")
PY

echo "==> pkgbuild (component)..."
pkgbuild \
    --root "$STAGE/root" \
    --component-plist "build/Component.plist" \
    --scripts "$STAGE/scripts" \
    --identifier com.vibecoding.virmic.pkg \
    --version "$VER" \
    --install-location / \
    --ownership recommended \
    "build/VibeCodingVirMic-component.pkg"

echo "==> productbuild (installer)..."
mkdir -p dist
productbuild \
    --distribution packaging/distribution.xml \
    --package-path build \
    --resources packaging/resources \
    "dist/VibeCodingVirMic-Installer-$VER.pkg"

if [ -f packaging/AppIcon.png ]; then
    echo "==> Setting installer's custom Finder icon..."
    .venv/bin/python packaging/set_pkg_icon.py packaging/AppIcon.png \
        "dist/VibeCodingVirMic-Installer-$VER.pkg" || echo "  (icon set skipped)"
fi

echo "==> Done: dist/VibeCodingVirMic-Installer-$VER.pkg"
ls -lh "dist/VibeCodingVirMic-Installer-$VER.pkg"
