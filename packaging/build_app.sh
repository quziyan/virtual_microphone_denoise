#!/usr/bin/env bash
# Build + ad-hoc sign the standalone "VibeCodingVirMic.app".
set -euo pipefail
cd "$(dirname "$0")/.."

# Mark dist/ as never-index BEFORE the .app appears, so Spotlight skips the loose
# build output (it would otherwise show as a duplicate of the installed app).
mkdir -p dist
touch dist/.metadata_never_index

echo "==> PyInstaller build..."
.venv/bin/pyinstaller packaging/VibeCodingVirMic.spec --noconfirm --distpath dist --workpath build

echo "==> ad-hoc codesign (deep, with entitlements)..."
codesign --force --deep --sign - --entitlements packaging/entitlements.plist "dist/VibeCodingVirMic.app"
codesign --verify --verbose=2 "dist/VibeCodingVirMic.app"

echo "==> Self-test (frozen bundle loads model)..."
"dist/VibeCodingVirMic.app/Contents/MacOS/VibeCodingVirMic" --selftest

# This loose .app is only the .pkg's payload source — unregister it from
# LaunchServices so it can't surface in Launchpad as a duplicate of the install.
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister"
[ -x "$LSREGISTER" ] && "$LSREGISTER" -u "$PWD/dist/VibeCodingVirMic.app" 2>/dev/null || true

echo "==> Built dist/VibeCodingVirMic.app"
