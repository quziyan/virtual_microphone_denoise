"""Launch-at-login support for VibeCodingVirMic.

Implemented as a per-user LaunchAgent — the standard, sandbox-friendly way to
make a menu-bar app start automatically when the user logs in. Enabling writes
a plist to ~/Library/LaunchAgents/ with RunAtLoad=true and registers it with
launchd; disabling unregisters and removes the plist.

The plist is the single source of truth: is_enabled() reports whether the file
exists. The launch target is resolved at write time so it works both from a
built .app bundle and from a source checkout (.venv/bin/python src/menubar.py).
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.vibecoding.virmic.login"
_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
_PLIST_PATH = _AGENTS_DIR / f"{LABEL}.plist"


def _service_target() -> str:
    """launchd service target for this user's GUI domain, e.g. gui/501/<LABEL>."""
    return f"gui/{os.getuid()}/{LABEL}"


def _launch_args() -> list[str]:
    """Argument vector launchd should run to start the app.

    Inside a packaged .app the frozen executable lives at
    ``…/VibeCodingVirMic.app/Contents/MacOS/VibeCodingVirMic`` and launching it
    directly is enough. From a source checkout sys.executable is the Python
    interpreter, so we hand it the menu-bar entry script.
    """
    exe = Path(sys.executable)
    if ".app/Contents/MacOS" in str(exe):
        return [str(exe)]
    script = Path(__file__).resolve().parent / "menubar.py"
    return [str(exe), str(script)]


def is_enabled() -> bool:
    """True when the login LaunchAgent is currently installed."""
    return _PLIST_PATH.exists()


def set_enabled(enabled: bool) -> None:
    """Install or remove the login LaunchAgent. Best-effort; never raises."""
    if enabled:
        _install()
    else:
        _remove()


def _install() -> None:
    try:
        _AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        plist = {
            "Label": LABEL,
            "ProgramArguments": _launch_args(),
            "RunAtLoad": True,
            # Relaunch on crash / abnormal exit so the BlackHole bridge comes back
            # on its own; a clean Quit (exit 0) is respected and stays quit.
            "KeepAlive": {"SuccessfulExit": False},
            "ProcessType": "Interactive",
        }
        with _PLIST_PATH.open("wb") as f:
            plistlib.dump(plist, f)
        # Clear any persistent "disabled" override left in launchd's database by a
        # previous _remove() (which uses `bootout`, but older builds used
        # `unload -w`) or by the user toggling this item off in System Settings.
        # Without this, a re-enable would write the plist yet launchd would still
        # skip it at login — the file exists but the override wins. `enable` only
        # clears that flag; it does NOT start the process, so no second copy is
        # launched. launchd auto-loads ~/Library/LaunchAgents/*.plist at the next
        # login, which is exactly when "launch at login" should take effect.
        _launchctl("enable", _service_target())
    except Exception:
        pass


def _remove() -> None:
    try:
        if _PLIST_PATH.exists():
            # `bootout` unregisters the agent for this session WITHOUT writing a
            # persistent "disabled" override. The old `unload -w` set that sticky
            # flag, which then survived a later re-enable and silently blocked the
            # agent from ever starting at login. Removing the plist is what makes
            # "disabled" durable here; no launchd override is needed or wanted.
            _launchctl("bootout", _service_target())
            _PLIST_PATH.unlink()
    except Exception:
        pass


def _launchctl(*args: str) -> None:
    """Run launchctl, swallowing errors. `enable`/`bootout` are the modern
    per-domain subcommands; a not-loaded service makes `bootout` error, which is
    harmless here. Failures never block the UI toggle."""
    try:
        subprocess.run(
            ["launchctl", *args],
            check=False, capture_output=True, timeout=5,
        )
    except Exception:
        pass
