#!/usr/bin/env bash
# Build + ad-hoc sign the standalone "VibeCodingVirMic.app".
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> PyInstaller build..."
.venv/bin/pyinstaller packaging/VibeCodingVirMic.spec --noconfirm --distpath dist --workpath build

echo "==> ad-hoc codesign (deep, with entitlements)..."
codesign --force --deep --sign - --entitlements packaging/entitlements.plist "dist/VibeCodingVirMic.app"
codesign --verify --verbose=2 "dist/VibeCodingVirMic.app"

echo "==> Self-test (frozen bundle loads model)..."
"dist/VibeCodingVirMic.app/Contents/MacOS/VibeCodingVirMic" --selftest

echo "==> Built dist/VibeCodingVirMic.app"
