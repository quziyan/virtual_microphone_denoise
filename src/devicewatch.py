"""CoreAudio hot-plug watcher.

Fires a callback whenever the set of audio devices changes (mic plugged /
unplugged / Bluetooth connected). This is the only way to learn about device
changes without re-initializing PortAudio on a timer — which would glitch a live
stream every cycle. We listen once and react only to real events.

The callback runs on a CoreAudio notification thread, so it must be cheap and
thread-safe: set a flag and let the app's main-thread timer do the real work
(PortAudio re-init, menu rebuild, engine restart).
"""

from __future__ import annotations

import ctypes
import ctypes.util


def _fourcc(s: str) -> int:
    return (ord(s[0]) << 24) | (ord(s[1]) << 16) | (ord(s[2]) << 8) | ord(s[3])


class _AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


_LISTENER_PROC = ctypes.CFUNCTYPE(
    ctypes.c_int32,                                  # OSStatus
    ctypes.c_uint32,                                 # AudioObjectID
    ctypes.c_uint32,                                 # inNumberAddresses
    ctypes.POINTER(_AudioObjectPropertyAddress),     # inAddresses
    ctypes.c_void_p,                                 # inClientData
)

_K_SYSTEM_OBJECT = 1
_K_DEVICES = _fourcc("dev#")          # kAudioHardwarePropertyDevices
_K_SCOPE_GLOBAL = _fourcc("glob")     # kAudioObjectPropertyScopeGlobal
_K_ELEMENT_MAIN = 0


class DeviceChangeWatcher:
    """Calls ``on_change()`` (cheaply, off-main-thread) on any device change."""

    def __init__(self, on_change) -> None:
        self._on_change = on_change
        self.active = False
        self._ca = None
        self._addr = None
        self._proc = None  # MUST keep a strong ref or the C callback is GC'd
        try:
            path = ctypes.util.find_library("CoreAudio") or (
                "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
            )
            self._ca = ctypes.CDLL(path)
            self._addr = _AudioObjectPropertyAddress(
                _K_DEVICES, _K_SCOPE_GLOBAL, _K_ELEMENT_MAIN
            )
            self._proc = _LISTENER_PROC(self._trampoline)
            self._ca.AudioObjectAddPropertyListener.argtypes = [
                ctypes.c_uint32,
                ctypes.POINTER(_AudioObjectPropertyAddress),
                _LISTENER_PROC,
                ctypes.c_void_p,
            ]
            self._ca.AudioObjectAddPropertyListener.restype = ctypes.c_int32
            status = self._ca.AudioObjectAddPropertyListener(
                _K_SYSTEM_OBJECT, ctypes.byref(self._addr), self._proc, None
            )
            self.active = status == 0
        except Exception:
            self.active = False

    def _trampoline(self, obj_id, n, addrs, client):  # CoreAudio thread
        try:
            self._on_change()
        except Exception:
            pass
        return 0

    def stop(self) -> None:
        if self._ca and self._proc and self.active:
            try:
                self._ca.AudioObjectRemovePropertyListener.argtypes = [
                    ctypes.c_uint32,
                    ctypes.POINTER(_AudioObjectPropertyAddress),
                    _LISTENER_PROC,
                    ctypes.c_void_p,
                ]
                self._ca.AudioObjectRemovePropertyListener(
                    _K_SYSTEM_OBJECT, ctypes.byref(self._addr), self._proc, None
                )
            except Exception:
                pass
            self.active = False
