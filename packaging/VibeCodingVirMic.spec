# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for "Hush Mic.app" — the standalone menu-bar virtual mic.

Build from the repo root:
    .venv/bin/pyinstaller packaging/HushMic.spec --noconfirm

Bundles: Python runtime, numpy, sounddevice (+ its own portaudio), rumps/pyobjc,
the Hush C library (libweya_nc.dylib) and ONNX model. No external deps required
on the target Mac. The BlackHole driver is installed separately by the .pkg.
"""

import os
import sys
from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.getcwd())

# Single source of truth for the version (src/version.py).
sys.path.insert(0, os.path.join(ROOT, "src"))
from version import __version__ as APP_VERSION  # noqa: E402

datas = [
    (os.path.join(ROOT, "vendor", "lib", "libweya_nc.dylib"), "vendor/lib"),
    (os.path.join(ROOT, "vendor", "models",
                  "advanced_dfnet16k_model_best_onnx.tar.gz"), "vendor/models"),
]
datas += collect_data_files("sounddevice")  # pulls in the bundled libportaudio

a = Analysis(
    [os.path.join(ROOT, "src", "menubar.py")],
    pathex=[os.path.join(ROOT, "src"), os.path.join(ROOT, "vendor")],
    binaries=[],
    datas=datas,
    hiddenimports=["engine", "weya_nc", "devicewatch", "tuning", "settingswindow",
                   "denoise_file", "updater", "version", "reporter", "reporter_config",
                   "numpy", "sounddevice", "rumps"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "PIL", "pandas", "scipy", "torch", "PyInstaller",
        # build-time / unused-at-runtime — safe to drop, shrinks the bundle and
        # so the first-launch Gatekeeper hash + cold read.
        "test", "unittest", "pydoc", "pydoc_data", "lib2to3", "distutils",
        "setuptools", "pip", "wheel", "sqlite3", "_sqlite3", "xmlrpc",
        "curses", "tkinter.test", "numpy.tests", "numpy.f2py",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VibeCodingVirMic",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,
    console=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=os.path.join(ROOT, "packaging", "entitlements.plist"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=False,
    name="VibeCodingVirMic",
)

app = BUNDLE(
    coll,
    name="VibeCodingVirMic.app",
    icon=os.path.join(ROOT, "packaging", "AppIcon.icns"),
    bundle_identifier="com.vibecoding.virmic",
    info_plist={
        "CFBundleName": "VibeCodingVirMic",
        "CFBundleDisplayName": "VibeCodingVirMic",
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "LSUIElement": True,  # menu-bar only, no Dock icon
        "LSMinimumSystemVersion": "12.0",
        "NSMicrophoneUsageDescription":
            "VibeCodingVirMic 需要访问麦克风,以实时去除背景人声与噪音。",
        "NSHighResolutionCapable": True,
    },
)
