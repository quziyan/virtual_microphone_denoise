"""Native (AppKit) Settings window for VibeCodingVirMic.

A real in-app window — not a web page. Built to hold multiple sections; for now
just "降噪调教 / Noise-reduction tuning": record a sample with background noise,
play it back at each suppression level, and click the best one (applied live).

Layout uses manual frames inside a flipped scroll view (top-left origin) for
predictability. Adding a future section = write another _build_*_section(y)
method and call it from _build().
"""

from __future__ import annotations

import threading

import objc
from AppKit import (
    NSButton, NSColor, NSFont, NSScrollView, NSSound, NSTextField, NSView, NSWindow,
)
from Foundation import NSMakeRect, NSObject, NSOperationQueue, NSTimer

import tuning

# NSWindowStyleMask / backing / bezel constants (numeric to avoid name drift).
_TITLED, _CLOSABLE, _RESIZABLE = 1 << 0, 1 << 1, 1 << 3
_BACKING_BUFFERED = 2
_BEZEL_ROUNDED = 1
W = 560  # window/content width


class _Flipped(NSView):
    def isFlipped(self):
        return True


def _label(parent, text, x, y, w, h, *, bold=False, size=13.0, secondary=False):
    f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    f.setStringValue_(text)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(False)
    f.setFont_(NSFont.boldSystemFontOfSize_(size) if bold
               else NSFont.systemFontOfSize_(size))
    if secondary:
        f.setTextColor_(NSColor.secondaryLabelColor())
    parent.addSubview_(f)
    return f


def _button(parent, title, x, y, w, h, target, action, tag=0):
    b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
    b.setTitle_(title)
    b.setBezelStyle_(_BEZEL_ROUNDED)
    b.setTarget_(target)
    b.setAction_(action)
    b.setTag_(tag)
    parent.addSubview_(b)
    return b


class SettingsController(NSObject):
    def initWithApp_(self, app):
        self = objc.super(SettingsController, self).init()
        if self is None:
            return None
        self._app = app
        self._window = None
        self._doc = None
        self._status = None
        self._secs = None
        self._sound = None
        self._levels = []
        self._row_views = []
        self._rows_top = 0
        self._countdown = None
        self._remaining = 0
        return self

    # -- lifecycle -----------------------------------------------------------

    @objc.python_method
    def show(self):
        if self._window is None:
            self._build()
        from AppKit import NSApp
        NSApp.activateIgnoringOtherApps_(True)
        self._window.makeKeyAndOrderFront_(None)

    @objc.python_method
    def _build(self):
        h = 620
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, h),
            _TITLED | _CLOSABLE | _RESIZABLE, _BACKING_BUFFERED, False,
        )
        win.setTitle_("VibeCodingVirMic 设置 / Settings")
        win.setReleasedWhenClosed_(False)  # we keep the controller; just hide
        win.center()

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, W, h))
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setDrawsBackground_(False)
        doc = _Flipped.alloc().initWithFrame_(NSMakeRect(0, 0, W, h))
        scroll.setDocumentView_(doc)
        win.setContentView_(scroll)

        self._window = win
        self._doc = doc

        y = 18
        y = self._build_tuning_section(y)
        doc.setFrame_(NSMakeRect(0, 0, W, max(h, y + 20)))

    # -- sections (add more here later) --------------------------------------

    @objc.python_method
    def _build_tuning_section(self, y):
        doc = self._doc
        _label(doc, "降噪调教 / Noise-reduction tuning", 18, y, W - 36, 24,
               bold=True, size=16.0)
        y += 30
        _label(doc, "录一段带背景人声的样本,逐档试听,选最适合你的强度(会立即应用)。",
               18, y, W - 36, 18, size=12.0, secondary=True)
        y += 28
        _label(doc, "时长(秒)", 18, y, 80, 22, size=12.0)
        self._secs = NSTextField.alloc().initWithFrame_(NSMakeRect(95, y - 2, 56, 24))
        self._secs.setStringValue_("6")
        doc.addSubview_(self._secs)
        _button(doc, "● 录制样本 Record", 170, y - 4, 180, 28, self, b"onRecord:")
        y += 38
        self._status = _label(doc, "提示:录制时同时说话 + 放背景声。", 18, y, W - 36, 18,
                              size=12.0, secondary=True)
        y += 26
        self._rows_top = y
        return y + 220  # reserve space; grows when rows are populated

    # -- actions -------------------------------------------------------------

    def onRecord_(self, sender):
        try:
            secs = float(self._secs.stringValue())
        except Exception:
            secs = 6.0
        secs = min(max(secs, 2.0), 30.0)
        sender.setEnabled_(False)
        app = self._app

        # Live countdown on the main thread while recording.
        self._remaining = int(round(secs))
        self._status.setStringValue_(f"🔴 录制中… 请说话 + 放背景声  {self._remaining}s")

        def tick(timer):
            self._remaining -= 1
            if self._remaining > 0:
                self._status.setStringValue_(f"🔴 录制中…  {self._remaining}s")
            else:
                self._status.setStringValue_("⏳ 各档位处理中 processing…")
                timer.invalidate()
                self._countdown = None

        self._countdown = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            1.0, True, tick)

        def work():
            err = None
            levels = None
            try:
                levels = tuning.record_and_process(
                    secs, app._resolve_input_match, app._tune_borrow_mic)
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"

            def ui():
                if self._countdown is not None:
                    self._countdown.invalidate()
                    self._countdown = None
                sender.setEnabled_(True)
                if err:
                    self._status.setStringValue_("❌ " + err)
                else:
                    self._status.setStringValue_("✅ 完成,逐个试听并选择")
                    self._populate(levels)

            NSOperationQueue.mainQueue().addOperationWithBlock_(ui)

        threading.Thread(target=work, daemon=True).start()

    def onPlay_(self, sender):
        i = int(sender.tag())
        if 0 <= i < len(self._levels):
            if self._sound is not None:
                try:
                    self._sound.stop()
                except Exception:
                    pass
            self._sound = NSSound.alloc().initWithContentsOfFile_byReference_(
                self._levels[i]["path"], True)
            if self._sound is not None:
                self._sound.play()

    def onUse_(self, sender):
        i = int(sender.tag())
        if 0 <= i < len(self._levels):
            level = self._levels[i]
            try:
                self._app._apply_mode(level["key"])
                self._status.setStringValue_(f"✅ 已应用:{level['label']}")
            except Exception as exc:
                self._status.setStringValue_(f"❌ {exc}")

    # -- ui helpers ----------------------------------------------------------

    @objc.python_method
    def _populate(self, levels):
        for v in self._row_views:
            v.removeFromSuperview()
        self._row_views = []
        self._levels = levels
        y = self._rows_top
        for i, L in enumerate(levels):
            sign = "+" if L["reduction_db"] > 0 else ""
            text = f"{L['label']}     vs 原始 {sign}{L['reduction_db']} dB"
            row = []
            row.append(_label(self._doc, text, 18, y + 6, 270, 20, size=13.0))
            row.append(_button(self._doc, "▶ 播放", 300, y, 90, 28,
                               self, b"onPlay:", tag=i))
            row.append(_button(self._doc, "用这个阈值", 398, y, 120, 28,
                               self, b"onUse:", tag=i))
            self._row_views += row
            y += 40
        self._doc.setFrame_(NSMakeRect(0, 0, W, max(620, y + 20)))
