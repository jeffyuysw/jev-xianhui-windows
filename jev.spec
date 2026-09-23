# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for 先回 (XianHui, Windows).

Produces a one-folder build under dist/XianHui. One-folder is chosen over
one-file because the OCR ONNX models and Qt plugins are large; one-file would
unpack several hundred MB to a temp dir on every launch.

    pyinstaller jev.spec --noconfirm
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = []
binaries = []
hiddenimports = [
    "rapidocr_onnxruntime",
    "onnxruntime",
    "cv2",
    "PIL",
    "numpy",
]
hiddenimports += collect_submodules("rapidocr_onnxruntime")

# RapidOCR ships its ONNX models and config as package data - must be bundled.
datas += collect_data_files("rapidocr_onnxruntime")
datas += collect_data_files("onnxruntime")

# 应用图标：运行时通过 resources.asset() 定位，需随包分发。
# 目标目录与源码中的相对路径保持一致（assets/...）。
datas += [("assets/app.ico", "assets"), ("assets/app_icon_256.png", "assets")]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="XianHui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # 应用图标：由根目录 icon.png 生成的多尺寸 .ico
    icon="assets/app.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="XianHui",
)
