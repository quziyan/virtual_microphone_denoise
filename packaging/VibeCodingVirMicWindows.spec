# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows executable.

Build from the repo root:
    .\.venv-win\Scripts\pyinstaller.exe packaging\VibeCodingVirMicWindows.spec --noconfirm
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.getcwd())

sys.path.insert(0, os.path.join(ROOT, "src"))
from version import __version__ as APP_VERSION  # noqa: E402

datas = [
    (
        os.path.join(ROOT, "vendor", "models", "advanced_dfnet16k_model_best_onnx.tar.gz"),
        "vendor/models",
    ),
]

windows_dll = os.path.join(ROOT, "vendor", "lib", "weya_nc.dll")
if os.path.exists(windows_dll):
    datas.append((windows_dll, "vendor/lib"))

datas += collect_data_files("sounddevice")

a = Analysis(
    [os.path.join(ROOT, "src", "windows_app.py")],
    pathex=[os.path.join(ROOT, "src"), os.path.join(ROOT, "vendor")],
    binaries=[],
    datas=datas,
    hiddenimports=["numpy", "sounddevice", "weya_nc", "version"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "PIL",
        "pandas",
        "scipy",
        "torch",
        "rumps",
        "PyObjCTools",
        "Foundation",
        "AppKit",
        "Quartz",
        "CoreAudio",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f"VibeCodingVirMic-Windows-{APP_VERSION}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
