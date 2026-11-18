# PyInstaller spec: one-folder build of the desktop app (Windows).
# Build with:  uv run --group build python tools/build_app.py
# ruff: noqa
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

datas = [(str(SRC / "simpledesign" / "i18n"), "simpledesign/i18n")]
datas += collect_data_files("ezdxf")  # fonts and resources
binaries = collect_dynamic_libs("OCP")

a = Analysis(
    [str(SRC / "simpledesign" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "vtkmodules.all",
        "vtkmodules.util.data_model",
        "vtkmodules.util.execution_model",
    ],
    excludes=["mypy", "mypyc", "tkinter", "IPython", "pytest", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SimpleDesign",
    console=bool(os.environ.get("SIMPLEDESIGN_CONSOLE")),  # debug builds
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="SimpleDesign", upx=False)
