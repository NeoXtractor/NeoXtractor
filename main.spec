# -*- mode: python ; coding: utf-8 -*-

import subprocess
import sys
import atexit

from PyInstaller.utils.hooks import collect_submodules

# Discovery-based packages (pkgutil.iter_modules) need their submodules
# collected explicitly: a frozen build imports nothing lazily, so an
# uncollected viewer or container module simply never appears.
_hidden = collect_submodules("neoxtractor.gui.viewers")
_hidden += collect_submodules("neoxtractor.core.packages")
_hidden += collect_submodules("neoxtractor.core.resources")

subprocess.call([sys.executable, 'tools/build_info_tool.py', 'gen'])

def cleanup():
    try:
        subprocess.call([sys.executable, 'tools/build_info_tool.py', 'del'])
    except Exception as e:
        print(f"Error during cleanup: {e}")

atexit.register(cleanup)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('configs', 'configs'), ('data', 'data')],
    hiddenimports=_hidden,
    hookspath=['hooks'],
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
    name='neoxtractor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
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
    name='main',
)
