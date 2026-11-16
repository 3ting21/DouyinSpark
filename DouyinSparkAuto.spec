# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the auto-send build: bundles Playwright's Node driver beside the exe."""

import os

import playwright
from PyInstaller.utils.hooks import collect_submodules

repo_root = os.path.dirname(os.path.abspath(SPEC))
driver_dir = os.path.join(os.path.dirname(playwright.__file__), "driver")

a = Analysis(
    [os.path.join(repo_root, "launcher.py")],
    pathex=[os.path.join(repo_root, "src")],
    binaries=[],
    datas=[(driver_dir, "playwright/driver")],
    hiddenimports=collect_submodules("playwright") + ["greenlet", "pyee"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter.test"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DouyinSpark",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
