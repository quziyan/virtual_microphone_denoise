"""Update checking for VibeCodingVirMic.

Polls a small JSON "appcast" for the latest published version and compares it to
the running version. Pure stdlib (urllib) so it bundles cleanly inside the frozen
.app — no extra dependencies. Network calls run on a daemon thread; the caller
passes a callback that it should marshal back onto the main thread itself
(the menubar does this via its 1 s rumps.Timer — see menubar._on_tick).

Appcast schema (hosted at DEFAULT_APPCAST_URL, e.g. GitHub raw of the repo)::

    {
      "version":  "1.1.0",                       # latest version, with/without 'v'
      "url":      "https://…/Installer-1.1.0.pkg | …/releases/latest",
      "notes":    "修了X、加了Y",                  # shown in the notification / dialog
      "min_os":   "12.0",                        # optional, informational
      "sha256":   "…",                           # optional, for future verified DL
      "mandatory": false,                        # optional, informational
      "pub_date": "2026-06-09"                   # optional
    }

This is the MVP "notify + open download" updater: on finding a newer version it
surfaces it in the menu + a macOS notification, and clicking opens the download
URL in the browser. It does NOT download or install — that stays the user's
existing double-click-the-.pkg flow (which already handles the BlackHole driver
and admin prompt). Silent download/install would require Developer-ID signing +
notarization, since anything the app downloads gets a Gatekeeper quarantine.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.request
from typing import Callable, Optional

from version import __version__

DEFAULT_APPCAST_URL = (
    "https://raw.githubusercontent.com/"
    "quziyan/virtual_microphone_denoise/master/appcast.json"
)


def appcast_url() -> str:
    """Resolve the appcast URL (env override for testing/self-hosting)."""
    return os.environ.get("VMIC_APPCAST_URL", DEFAULT_APPCAST_URL)


def parse_version(v: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2' -> (1, 2, 3) / (1, 2). Non-numeric tails are ignored."""
    s = (v or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in s.split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break  # stop at first non-digit (e.g. '3-beta' -> 3)
        parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def is_newer(latest: str, current: str) -> bool:
    """True iff `latest` is a strictly higher version than `current`."""
    a, b = parse_version(latest), parse_version(current)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def fetch_appcast(url: Optional[str] = None, timeout: float = 8.0) -> dict:
    """GET + parse the appcast JSON. Raises on network/parse error."""
    req = urllib.request.Request(
        url or appcast_url(),
        headers={"User-Agent": f"VibeCodingVirMic/{__version__}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8"))


def check_for_update(
    current: str = __version__, url: Optional[str] = None
) -> Optional[dict]:
    """Return the appcast dict if a newer version exists, else None.

    Never raises: any network/parse failure returns None (treated as "no update
    info"), so a flaky connection never breaks the app. Callers that need to
    distinguish "up to date" from "check failed" should use check_async, which
    reports the error separately.
    """
    try:
        info = fetch_appcast(url)
    except Exception:
        return None
    latest = str(info.get("version", ""))
    return info if latest and is_newer(latest, current) else None


def check_async(
    on_result: Callable[[Optional[dict], Optional[str]], None],
    current: str = __version__,
    url: Optional[str] = None,
) -> None:
    """Run the check on a daemon thread, then invoke on_result(info, error).

    on_result is called with exactly one of:
      - (info_dict, None)  -> a newer version is available
      - (None, None)       -> up to date
      - (None, "message")  -> the check failed (network/parse)

    on_result runs on the worker thread; the caller is responsible for marshaling
    any UI work back onto the main thread.
    """

    def _run() -> None:
        try:
            info = fetch_appcast(url)
        except Exception as exc:  # network down, bad JSON, etc.
            on_result(None, f"{type(exc).__name__}: {exc}")
            return
        latest = str(info.get("version", ""))
        if latest and is_newer(latest, current):
            on_result(info, None)
        else:
            on_result(None, None)

    threading.Thread(target=_run, name="vmic-update-check", daemon=True).start()
