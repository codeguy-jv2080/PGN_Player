# Third-party notices

PGN Player is distributed under GPL-3.0-or-later; see LICENSE. The application
icon is original artwork supplied with this source under the same license.

- python-chess 1.11.2, copyright Niklas Fiekas and contributors: GPL-3.0-or-later.
  Source: https://github.com/niklasf/python-chess/tree/v1.11.2
- Chess piece SVG artwork: Colin M. L. Burnett. The python-chess piece set is
  offered under GFDL, BSD, and GPL terms; this distribution uses its GPL option.
  Attribution: https://python-chess.readthedocs.io/en/latest/svg.html
- PySide6 Essentials and Shiboken 6.11.2, copyright The Qt Company and
  contributors: used under LGPLv3. The included Qt libraries remain separate
  dynamic libraries and may be replaced with compatible modified versions.
  Qt for Python source: https://code.qt.io/cgit/pyside/pyside-setup.git/
  Qt source archives: https://download.qt.io/official_releases/qt/6.11/6.11.2/
  LGPL and GPL license texts are in licenses/. Any commercial license text
  included by upstream wheels describes an alternative licensing option; a
  commercial license is not required to use this open-source application.
- CPython runtime: Python Software Foundation License. The exact bundled
  version is recorded in licenses/runtime/VERSIONS.txt. Its license and
  historical notices are in licenses/runtime/Python.txt. Source:
  https://www.python.org/downloads/source/
- SQLite, included with Python: public domain. https://sqlite.org/copyright.html
- PyInstaller 6.22.3: the bundled bootloader and related loader files use
  GPL-2.0-or-later with the upstream bootloader exception. Runtime hooks and
  additional runtime helpers use Apache-2.0. The complete upstream copyright
  notices and licensing terms are copied verbatim from the pinned distribution
  into licenses/runtime/pyinstaller/COPYING.txt. Version-specific upstream text:
  https://github.com/pyinstaller/pyinstaller/blob/v6.22.3/COPYING.txt

Build and test tools (pytest, pytest-qt, Pillow, packaging and PyInstaller's
build modules) are development dependencies and are not application features.
Their versions are recorded in requirements.lock. Upstream license notices
available from installed runtime package metadata are copied into
licenses/runtime by scripts/prepare_assets.py. The same script writes
licenses/runtime/VERSIONS.txt with the exact Python, SQLite, chess, Qt package
and PyInstaller versions used for that build, without local paths or user data.

Chess PGN Master is a behavioral reference only. PGN Player has its own source,
interface, branding and artwork; it is not affiliated with its developer.
