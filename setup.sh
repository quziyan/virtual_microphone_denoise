#!/usr/bin/env bash
# One-time setup for the Hush virtual microphone (macOS, Apple Silicon).
#
# Installs:
#   - BlackHole 2ch  (the virtual audio device; cask install needs your password)
#   - PortAudio      (so Python's sounddevice can do real-time audio)
#   - a Python venv with numpy + sounddevice
# Verifies the vendored Hush binaries and de-quarantines the dylib.
#
# Run:  bash setup.sh
set -euo pipefail

cd "$(dirname "$0")"
echo "==> Hush virtual microphone setup"

# --- 0. sanity ---------------------------------------------------------------
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "ERROR: the vendored libweya_nc.dylib is Apple-Silicon (arm64) only." >&2
  echo "       Your arch is $(uname -m)." >&2
  exit 1
fi
command -v brew >/dev/null || { echo "ERROR: Homebrew not found. https://brew.sh"; exit 1; }

# --- 1. BlackHole (virtual device) ------------------------------------------
if ls /Library/Audio/Plug-Ins/HAL/ 2>/dev/null | grep -qi blackhole; then
  echo "==> BlackHole already installed."
else
  echo "==> Installing BlackHole 2ch (you'll be asked for your password)..."
  brew install --cask blackhole-2ch
fi

# --- 2. PortAudio (for sounddevice) -----------------------------------------
if brew list portaudio >/dev/null 2>&1; then
  echo "==> PortAudio already installed."
else
  echo "==> Installing PortAudio..."
  brew install portaudio
fi

# --- 3. Python venv + deps ---------------------------------------------------
if [[ ! -d .venv ]]; then
  echo "==> Creating venv..."
  python3 -m venv .venv
fi
echo "==> Installing Python deps..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# --- 4. Verify vendored Hush binaries ---------------------------------------
echo "==> Verifying vendored model binaries..."
DYLIB=vendor/lib/libweya_nc.dylib
MODEL=vendor/models/advanced_dfnet16k_model_best_onnx.tar.gz
[[ -f "$DYLIB" ]] || { echo "ERROR: missing $DYLIB"; exit 1; }
[[ -f "$MODEL" ]] || { echo "ERROR: missing $MODEL"; exit 1; }
file "$DYLIB" | grep -q "Mach-O.*arm64" || { echo "ERROR: $DYLIB is not an arm64 Mach-O"; exit 1; }
# Downloaded dylibs are Gatekeeper-quarantined; clear it so ctypes can dlopen.
xattr -dr com.apple.quarantine "$DYLIB" 2>/dev/null || true

# --- 5. Smoke test the model (offline) --------------------------------------
echo "==> Running offline pipeline test..."
.venv/bin/python tests/test_pipeline.py

cat <<'EOF'

==> Setup complete.

Start the virtual mic — menu-bar app (recommended):
    .venv/bin/python src/menubar.py

...or the command line:
    .venv/bin/python src/vmic.py

Then in Zoom / Meet / Teams / Discord, choose "BlackHole 2ch" as the microphone.
First run prompts for microphone permission for your terminal — allow it.

Tip: confirm BlackHole's sample rate is 48000 Hz in "Audio MIDI Setup" (matches
the vmic default). Use `--samplerate` to override.
EOF
