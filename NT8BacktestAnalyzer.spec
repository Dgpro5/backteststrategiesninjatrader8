# -*- mode: python ; coding: utf-8 -*-
# Build dell'eseguibile singolo:  python -m PyInstaller --noconfirm --clean NT8BacktestAnalyzer.spec
import sys

from PyInstaller.utils.hooks import collect_data_files

datas = [("nt8analyzer/assets/icon.png", "nt8analyzer/assets")]
datas += collect_data_files("pyqtgraph", includes=["icons/**/*"])

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "pandas",
        "IPython",
        "OpenGL",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "pyqtgraph.opengl",
        "pyqtgraph.examples",
        "pyqtgraph.jupyter",
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
    name="NT8BacktestAnalyzer",
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    icon="nt8analyzer/assets/icon.ico" if sys.platform == "win32" else None,
)
