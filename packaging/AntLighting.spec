# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AntLighting VPN.

Build with:

    pyinstaller packaging/AntLighting.spec --noconfirm

The result is ``dist/AntLighting/AntLighting.exe`` plus the Qt runtime.  The
Xray-core binary and its geo data files are *not* bundled by this spec — the
build workflow downloads the official release and drops it next to the
executable, which keeps the licence separation clean and lets the core be
updated without rebuilding the app.
"""

import os
import sys

SPECPATH = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.dirname(SPECPATH)
UI_DIR = os.path.join(ROOT, "antlighting", "ui")

block_cipher = None

datas = [
    (UI_DIR, "antlighting/ui"),
]

# The QML engine resolves `import QtQuick*` from Qt's qml directory.  PyInstaller's
# PySide6 hooks copy the C++ Qt libraries but not the QML *modules*, so a frozen
# QtQuick app fails at runtime with "module QtQuick is not installed".  Bundle the
# qml tree explicitly (only the modules we actually import, to stay lean).
try:
    import PySide6

    _qml_root = os.path.join(os.path.dirname(PySide6.__file__), "Qt", "qml")
    _wanted_qml = [
        "QtQml",
        "QtQuick",
        "QtQuick.Controls",
        "QtQuick.Layouts",
        "QtQuick.Shapes",
        "QtQuick.Window",
        "QtQuick.Templates",
    ]
    if os.path.isdir(_qml_root):
        for _mod in _wanted_qml:
            _src = os.path.join(_qml_root, _mod)
            if os.path.isdir(_src):
                datas.append((_src, os.path.join("PySide6", "Qt", "qml", _mod)))
except Exception:  # noqa: BLE001 - never break the build on introspection
    pass

# An optional, pre-built core placed in packaging/runtime is bundled too.
runtime_dir = os.path.join(SPECPATH, "runtime")
if os.path.isdir(runtime_dir):
    for name in os.listdir(runtime_dir):
        path = os.path.join(runtime_dir, name)
        if os.path.isfile(path):
            datas.append((path, "."))

icon = os.path.join(SPECPATH, "AntLighting.ico")
if not os.path.isfile(icon):
    icon = None

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "antlighting",
        "antlighting.app",
        "antlighting.controller",
        "antlighting.collector",
        "antlighting.core.xray",
        "antlighting.core.downloader",
        "antlighting.sources.github",
        "antlighting.sources.localfile",
        "antlighting.sources.telegram",
        "antlighting.net.tunnel",
        "antlighting.net.system_proxy",
        "antlighting.net.elevation",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # QtWebEngine / Qt3D / QtMultimedia and friends are never imported by
    # AntLighting; excluding them keeps the bundle well under half its size.
    excludes=[
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtWebChannel",
        "PySide6.QtWebSockets",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtQuick3D",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.QtPositioning",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtTest",
        "tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AntLighting",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # CI builds a console variant (ANTLIGHTING_CONSOLE=1) so startup errors from
    # the Qt C++ layer are capturable; release builds stay console-less.
    console=os.environ.get("ANTLIGHTING_CONSOLE", "") == "1",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
    version=os.path.join(SPECPATH, "version_info.txt")
    if os.path.isfile(os.path.join(SPECPATH, "version_info.txt"))
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AntLighting",
)
