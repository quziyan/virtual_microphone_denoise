#!/usr/bin/env python3
"""Unit tests for the update-checker version logic (no network).

Run:  .venv/bin/python tests/test_updater.py    # -> PASS / FAIL
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import updater  # noqa: E402


def test_parse_version() -> None:
    assert updater.parse_version("1.2.3") == (1, 2, 3)
    assert updater.parse_version("v1.2.3") == (1, 2, 3)
    assert updater.parse_version("1.2") == (1, 2)
    assert updater.parse_version("1.0.0-beta") == (1, 0, 0)
    assert updater.parse_version("") == (0,)


def test_is_newer() -> None:
    # strictly greater
    assert updater.is_newer("1.1.0", "1.0.0")
    assert updater.is_newer("1.0.1", "1.0.0")
    assert updater.is_newer("2.0.0", "1.9.9")
    assert updater.is_newer("v1.2.0", "1.1.5")
    # different lengths, zero-padded
    assert updater.is_newer("1.0.1", "1.0")
    # equal or older -> not newer
    assert not updater.is_newer("1.0.0", "1.0.0")
    assert not updater.is_newer("1.0", "1.0.0")
    assert not updater.is_newer("1.0.0", "1.1.0")
    assert not updater.is_newer("0.9.9", "1.0.0")


def test_check_for_update_offline(monkeypatch=None) -> None:
    # When the appcast advertises an OLDER/EQUAL version, no update is returned;
    # when NEWER, the dict comes back. Stub fetch to avoid the network.
    def fake_fetch(url=None, timeout=8.0):
        return {"version": "0.9.0", "url": "x"}

    orig = updater.fetch_appcast
    try:
        updater.fetch_appcast = fake_fetch
        assert updater.check_for_update(current="1.0.0") is None
        updater.fetch_appcast = lambda url=None, timeout=8.0: {"version": "1.2.0", "url": "x"}
        info = updater.check_for_update(current="1.0.0")
        assert info and info["version"] == "1.2.0"
        # network failure -> None, never raises
        def boom(url=None, timeout=8.0):
            raise OSError("offline")
        updater.fetch_appcast = boom
        assert updater.check_for_update(current="1.0.0") is None
    finally:
        updater.fetch_appcast = orig


def main() -> int:
    tests = [test_parse_version, test_is_newer, test_check_for_update_offline]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {t.__name__}: {exc}")
    print("UPDATER TESTS:", "PASS" if not failed else f"FAIL ({failed})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
