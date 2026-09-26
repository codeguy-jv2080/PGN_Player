# A clean, edition-neutral application payload. Edition is explicit beside exe.
from pathlib import Path
import os
import sys

root = Path(SPECPATH).parent
windows = Path(os.environ['WINDIR'])
os.environ['PATH'] = os.pathsep.join(map(str, (Path(sys.executable).parent, Path(sys.base_prefix), Path(sys.base_prefix) / 'DLLs', windows / 'System32', windows)))
analysis = Analysis(
    [str(root / 'src' / 'pgn_player' / '__main__.py')],
    pathex=[str(root / 'src')],
    datas=[
        (str(root / 'src' / 'pgn_player' / 'assets'), 'pgn_player/assets'),
        (str(root / 'LICENSE'), '.'),
        (str(root / 'THIRD_PARTY_NOTICES.md'), '.'),
        (str(root / 'licenses'), 'licenses'),
    ],
    hiddenimports=['PySide6.QtSvg'],
    excludes=['tkinter', 'pytest', 'pytestqt', 'PIL', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtTest'],
    noarchive=False,
)
# Refuse unrelated host tool DLLs even when a host injects extra search paths.
allowed_binary_roots = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), windows.resolve()]
for destination, source, kind in analysis.binaries:
    if not any(Path(source).resolve().is_relative_to(base) for base in allowed_binary_roots):
        raise RuntimeError(f'Unexpected dependency outside Python/Windows: {Path(source).name}')
archive = PYZ(analysis.pure)
exe = EXE(
    archive, analysis.scripts, [], exclude_binaries=True,
    name='PGN Player', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    icon=str(root / 'src' / 'pgn_player' / 'assets' / 'app.ico'),
)
collection = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='PGN Player')
