"""Generate the original application icon and collect local runtime notices."""
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import struct
import sys
from importlib.metadata import distribution


def collect_runtime_notices(root: Path) -> None:
    """Copy upstream notices intact and record only public runtime versions."""
    packages = {
        name: distribution(name)
        for name in ("chess", "PySide6-Essentials", "shiboken6", "pyinstaller")
    }
    notices = root / "licenses" / "runtime"
    copies = []
    for name in ("PySide6-Essentials", "shiboken6", "pyinstaller"):
        package = packages[name]
        sources = [
            (item, package.locate_file(item))
            for item in package.files or []
            if "licenses" in item.parts and package.locate_file(item).is_file()
        ]
        if name == "pyinstaller" and not any(item.name == "COPYING.txt" for item, _ in sources):
            raise RuntimeError("PyInstaller COPYING.txt is missing from the installed package; notices cannot be prepared.")
        copies.extend((source, notices / name / item.name) for item, source in sources)

    notices.mkdir(parents=True, exist_ok=True)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, notices / "Python.txt")
    for source, destination in copies:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    versions = [
        "Runtime and packaging versions for this build",
        f"CPython: {platform.python_version()}",
        f"Architecture: {struct.calcsize('P') * 8}-bit",
        f"SQLite: {sqlite3.sqlite_version}",
        *(f"{name}: {package.version}" for name, package in packages.items()),
    ]
    (notices / "VERSIONS.txt").write_text("\n".join(versions) + "\n", encoding="utf-8")


def prepare_icon(root: Path) -> None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PIL import Image

    app = QGuiApplication([])
    canvas = QImage(256, 256, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    QSvgRenderer(str(root / "src/pgn_player/assets/app.svg")).render(painter)
    painter.end()
    cache = root / ".build"
    cache.mkdir(exist_ok=True)
    canvas.save(str(cache / "app.png"))
    with Image.open(cache / "app.png") as raster:
        raster.save(root / "src/pgn_player/assets/app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[1]
    collect_runtime_notices(project_root)
    prepare_icon(project_root)
    print("Icon and dependency notices prepared.")
