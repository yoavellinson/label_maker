# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path.cwd()


a = Analysis(
    ["label_maker_desktop.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        ("fonts", "fonts"),
        ("grid", "grid"),
        ("blends.csv", "."),
        ("textures", "textures"),
    ],
    hiddenimports=[
        "bidi.algorithm",
        "PIL._tkinter_finder",
        "pypdfium2",
        "pypdfium2_raw",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LabelMaker_v2",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LabelMaker_v2",
)
