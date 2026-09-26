"""Run PyInstaller with explicit local cache and dependency search paths."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
os.environ["PYINSTALLER_CONFIG_DIR"] = str(root / ".build" / "pyinstaller-cache")
windows = Path(os.environ["WINDIR"])
os.environ["PATH"] = os.pathsep.join(map(str, (
    Path(sys.executable).parent,
    Path(sys.base_prefix),
    Path(sys.base_prefix) / "DLLs",
    windows / "System32",
    windows,
)))

from PyInstaller.__main__ import run

run(sys.argv[1:])
