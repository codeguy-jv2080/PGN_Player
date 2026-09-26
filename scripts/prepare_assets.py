"""Generate the original application icon and collect local runtime notices."""
import os
from pathlib import Path
import shutil
import sys
from importlib.metadata import distribution

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PIL import Image

root = Path(__file__).resolve().parents[1]
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
notices = root / "licenses" / "runtime"
notices.mkdir(parents=True, exist_ok=True)
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    shutil.copy2(python_license, notices / "Python.txt")
for name in ("PySide6-Essentials", "shiboken6"):
    package = distribution(name)
    for item in package.files or []:
        if "licenses" in item.parts and package.locate_file(item).is_file():
            destination = notices / name / item.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(package.locate_file(item), destination)
print("Icon and dependency notices prepared.")
