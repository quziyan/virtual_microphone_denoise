#!/usr/bin/env python3
"""macOS menu-bar app for the VibeCodingVirMic virtual microphone.

A tiny status-bar control: start/stop the virtual mic, pick the physical
microphone, and flip suppression strength mid-call without touching the terminal.

Menu:
    ● VibeCodingVirMic            <- title; ● = running, ○ = stopped
    ─────────────
    <status line>                 <- live mic → output · SNR · underruns
    Start / Stop
    ─────────────
    麦克风 / Microphone ▸          <- pick the physical mic (BlackHole excluded)
    ─────────────
    Off (passthrough) / Gentle / Aggressive   <- suppression (radio)
    ─────────────
    Quit

Choice of mic + mode is remembered in
~/Library/Application Support/VibeCodingVirMic/config.json.

Run:  .venv/bin/python src/menubar.py
First run prompts for microphone permission — allow it.
VMIC_INPUT / VMIC_OUTPUT env vars still override device matching if set.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import rumps
except ImportError:
    raise SystemExit(
        "rumps not installed. Run: .venv/bin/pip install rumps  "
        "(or re-run `bash setup.sh`)."
    )

# engine pulls numpy + the Hush wrapper; import it lazily so launch (showing the
# menu-bar icon) doesn't pay for numpy/sounddevice. devicewatch is ctypes-only.
from devicewatch import DeviceChangeWatcher

APP_NAME = "VibeCodingVirMic"     # bundle / config / dialogs
MENU_NAME = "VCVMic"              # short label shown in the macOS menu bar
MODES = [
    ("Off (passthrough)", "off"),
    ("Gentle (20 dB)", "gentle"),
    ("Medium (40 dB)", "db40"),
    ("Strong (60 dB)", "db60"),
    ("Aggressive (100 dB)", "aggressive"),
]
RUNNING_GLYPH = "●"
STOPPED_GLYPH = "○"

CONFIG_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass


class HushMicApp(rumps.App):
    def __init__(self) -> None:
        super().__init__(f"{STOPPED_GLYPH} {MENU_NAME}", quit_button=None)

        cfg = load_config()
        self._input_name: str | None = cfg.get("input")  # chosen mic name, or None
        self._start_mode = cfg.get("mode", "gentle")  # default 20 dB
        self._engine: VMicEngine | None = None
        self._error: str | None = None
        self._dev_signature: tuple = ()   # last-seen input-device name set
        self._tick = 0
        self._dev_dirty = False           # set by CoreAudio watcher on any change
        self._mics_initialized = False    # first device scan deferred off launch
        self._settings = None             # lazily-created native Settings window

        # Build menu items.
        self._status_item = rumps.MenuItem("Stopped")
        self._status_item.set_callback(None)  # display only
        self._toggle_item = rumps.MenuItem("Start", callback=self._on_toggle)

        self._mic_menu = rumps.MenuItem("麦克风 / Microphone")
        self._mic_items: dict[str, rumps.MenuItem] = {}

        self._mode_items: dict[str, rumps.MenuItem] = {}
        for label, key in MODES:
            self._mode_items[key] = rumps.MenuItem(label, callback=self._make_mode_cb(key))

        self.menu = [
            self._status_item,
            self._toggle_item,
            None,
            self._mic_menu,
            None,
            *[self._mode_items[key] for _, key in MODES],
            None,
            rumps.MenuItem("设置 / Settings…", callback=self._on_settings),
            None,
            rumps.MenuItem("Quit", callback=self._on_quit),
        ]
        # Placeholder; the real scan runs on the first tick so launch stays fast
        # (sounddevice/PortAudio init is off the critical path).
        self._mic_menu.add(rumps.MenuItem("扫描中… / scanning"))
        self._refresh_mode_checks(self._start_mode)
        self._update_ui()

        # Hot-plug: CoreAudio fires on real device changes; the callback only
        # sets a flag, the main-thread timer does the work.
        self._watcher = DeviceChangeWatcher(self._on_devices_changed)

        self._timer = rumps.Timer(self._on_tick, 1)
        self._timer.start()

    def _on_devices_changed(self) -> None:
        """Called on a CoreAudio thread — keep it trivial and thread-safe."""
        self._dev_dirty = True

    # -- config --------------------------------------------------------------

    def _save(self) -> None:
        save_config({"input": self._input_name, "mode": self._start_mode})

    # -- engine helpers ------------------------------------------------------

    def _resolve_input_match(self):
        """What to feed VMicEngine as the input device.

        Priority: VMIC_INPUT env > saved choice > safe auto-default (system
        default mic, never BlackHole).
        """
        env = os.environ.get("VMIC_INPUT")
        if env:
            return env
        if self._input_name:
            return self._input_name
        try:
            import sounddevice as sd
            from engine import default_input_match
            return default_input_match(sd)
        except Exception:
            return None

    def _ensure_engine(self) -> VMicEngine:
        if self._engine is None:
            from engine import VMicEngine
            self._engine = VMicEngine(
                input_match=self._resolve_input_match(),
                output_match=os.environ.get("VMIC_OUTPUT"),  # None -> BlackHole
                mode=self._start_mode,
            )
        return self._engine

    @property
    def _running(self) -> bool:
        return self._engine is not None and self._engine.running

    # -- callbacks -----------------------------------------------------------

    def _on_toggle(self, _sender) -> None:
        try:
            if self._running:
                self._engine.stop()
            else:
                self._ensure_engine().start()
            self._error = None
        except LookupError as exc:
            self._error = str(exc)
            rumps.alert(APP_NAME, str(exc))
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            rumps.alert(f"{APP_NAME} — error", self._error)
        self._update_ui()

    def _make_mic_cb(self, name: str):
        def cb(_sender) -> None:
            self._input_name = name
            self._save()
            if self._engine is not None:
                try:
                    self._engine.set_input(name)
                except Exception as exc:
                    self._error = f"{type(exc).__name__}: {exc}"
                    rumps.alert(f"{APP_NAME} — error", self._error)
            self._refresh_mic_checks(name)
            self._update_ui()
        return cb

    def _make_mode_cb(self, key: str):
        return lambda _sender: self._apply_mode(key)

    def _apply_mode(self, key: str) -> None:
        """Apply a suppression preset (from a menu click or the tuning page)."""
        self._start_mode = key
        self._save()
        if self._engine is not None:
            try:
                self._engine.set_mode(key)
            except Exception as exc:
                rumps.alert(f"{APP_NAME} — error", f"{type(exc).__name__}: {exc}")
        self._refresh_mode_checks(key)

    # -- settings / tuning ---------------------------------------------------

    def _on_settings(self, _sender) -> None:
        try:
            if self._settings is None:
                from settingswindow import SettingsController  # lazy: pulls numpy/AppKit
                self._settings = SettingsController.alloc().initWithApp_(self)
            self._settings.show()
        except Exception as exc:
            rumps.alert(f"{APP_NAME} — error", f"{type(exc).__name__}: {exc}")

    def _tune_borrow_mic(self, acquire: bool, token):
        """Free the mic for a tuning recording, then restore the engine after.

        Called from the tuning server's thread; engine methods are lock-guarded.
        """
        if acquire:
            was_running = self._running
            if was_running and self._engine is not None:
                try:
                    self._engine.stop()
                except Exception:
                    pass
            return was_running
        if token and self._engine is not None:
            try:
                self._engine.start()
            except Exception:
                pass
        return None

    def _on_quit(self, _sender) -> None:
        try:
            self._watcher.stop()
        except Exception:
            pass
        if self._engine is not None:
            self._engine.stop()
        rumps.quit_application()

    def _on_tick(self, _timer) -> None:
        if not self._mics_initialized:
            # First scan: enumerate mics now (deferred from launch).
            self._mics_initialized = True
            self._rebuild_mic_menu()
            self._update_ui()
            return
        if self._dev_dirty:
            self._dev_dirty = False
            self._handle_device_change()
        elif not self._watcher.active:
            # Fallback when the CoreAudio listener didn't register: poll every
            # ~3s, but only while stopped (re-init would glitch a live stream).
            self._tick = (self._tick + 1) % 3
            if self._tick == 0 and not self._running:
                self._maybe_refresh_mics()
        self._update_ui()

    def _handle_device_change(self) -> None:
        """A device was (un)plugged. Refresh the list; if the in-use mic vanished
        switch to a system default, otherwise keep the current mic.

        Re-initialising PortAudio (required to see the new device list) tears
        down live streams, so a running engine is stopped and restarted — onto
        the same mic for an unrelated change, or onto a fallback if its mic left.
        """
        import sounddevice as sd
        from engine import default_input_match
        was_running = self._running
        current = self._engine.in_name if (was_running and self._engine) else None
        if was_running:
            try:
                self._engine.stop()
            except Exception:
                pass
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        self._rebuild_mic_menu()
        if not (was_running and self._engine is not None):
            return
        names = list(self._mic_items.keys())
        if current in names:
            target = current                      # new/unrelated device: keep mic
        else:
            di = default_input_match(sd)           # in-use mic disconnected: switch
            target = sd.query_devices(di)["name"] if di is not None else None
            self._input_name = target
            self._save()
            self._refresh_mic_checks(target)
        try:
            self._engine.set_input(target)         # engine stopped -> just sets field
            self._engine.start()
            self._error = None
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"

    def _maybe_refresh_mics(self) -> None:
        """Fallback list refresh (stopped only): re-enumerate, rebuild if changed."""
        try:
            import sounddevice as sd
            from engine import list_input_devices
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                pass
            names = tuple(n for _, n in list_input_devices(sd))
        except Exception:
            return
        if names != self._dev_signature:
            self._rebuild_mic_menu()

    # -- ui ------------------------------------------------------------------

    def _rebuild_mic_menu(self) -> None:
        """Populate the mic submenu with current input devices (no BlackHole)."""
        for key in list(self._mic_menu.keys()):
            del self._mic_menu[key]
        self._mic_items = {}
        try:
            import sounddevice as sd
            from engine import list_input_devices
            devices = list_input_devices(sd)
            current = self._selected_input_name(sd, devices)
        except Exception as exc:
            self._dev_signature = ()
            self._mic_menu.add(rumps.MenuItem(f"(无法枚举设备: {exc})"))
            return
        self._dev_signature = tuple(n for _, n in devices)
        if not devices:
            self._mic_menu.add(rumps.MenuItem("(未找到麦克风)"))
            return
        for _idx, name in devices:
            item = rumps.MenuItem(name, callback=self._make_mic_cb(name))
            item.state = 1 if name == current else 0
            self._mic_menu.add(item)
            self._mic_items[name] = item

    def _selected_input_name(self, sd, devices):
        from engine import default_input_match
        names = [n for _, n in devices]
        if self._input_name and self._input_name in names:
            return self._input_name
        di = default_input_match(sd)
        if di is not None:
            try:
                return sd.query_devices(di)["name"]
            except Exception:
                pass
        return names[0] if names else None

    def _refresh_mic_checks(self, active: str) -> None:
        for name, item in self._mic_items.items():
            item.state = 1 if name == active else 0

    def _refresh_mode_checks(self, active: str) -> None:
        for key, item in self._mode_items.items():
            item.state = 1 if key == active else 0

    def _update_ui(self) -> None:
        running = self._running
        self.title = f"{RUNNING_GLYPH if running else STOPPED_GLYPH} {MENU_NAME}"
        self._toggle_item.title = "Stop" if running else "Start"
        if self._error and not running:
            self._status_item.title = f"Error: {self._error[:48]}"
        elif running:
            s = self._engine.stats
            self._status_item.title = (
                f"{self._engine.in_name} → {self._engine.out_name} · "
                f"{self._engine.mode} · SNR {s['snr']:+.1f} dB · under {s['under']}"
            )
        else:
            mic = self._input_name or "自动 auto"
            self._status_item.title = f"Stopped · mic: {mic}"


def _selftest() -> int:
    """Headless check that the bundled Hush model loads and processes a frame."""
    import numpy as np
    from engine import LIB_PATH, MODEL_PATH
    from weya_nc import WeyaNC

    print(f"LIB_PATH   = {LIB_PATH}  exists={LIB_PATH.exists()}")
    print(f"MODEL_PATH = {MODEL_PATH}  exists={MODEL_PATH.exists()}")
    nc = WeyaNC(lib_path=str(LIB_PATH), model_path=str(MODEL_PATH),
                sample_rate=48000, atten_lim_db=100.0)
    frame = np.zeros(nc.frame_length, dtype=np.float32)
    out = nc.process_frame(frame)
    nc.close()
    ok = out.shape == frame.shape
    print(f"frame_length={nc.frame_length}  processed_ok={ok}")
    print("SELFTEST PASS" if ok else "SELFTEST FAIL")
    return 0 if ok else 1


def _selftest_ui() -> int:
    """Headless check that the native Settings window can be built (AppKit bundled)."""
    from AppKit import NSApplication
    NSApplication.sharedApplication()
    from settingswindow import SettingsController

    class _Fake:
        def _resolve_input_match(self):
            return None

        def _tune_borrow_mic(self, a, t):
            return None

        def _apply_mode(self, k):
            pass

    c = SettingsController.alloc().initWithApp_(_Fake())
    c._build()
    c._populate([{"key": "off", "label": "x", "reduction_db": 0.0, "path": "/tmp/x.wav"}])
    print(f"UI SELFTEST PASS: {c._window.title()}")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    if "--selftest-ui" in sys.argv:
        sys.exit(_selftest_ui())
    HushMicApp().run()

