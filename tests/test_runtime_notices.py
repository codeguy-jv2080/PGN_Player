"""Check license collection with synthetic package metadata and no Qt startup."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def notice_collector(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "prepare_assets.py"
    spec = importlib.util.spec_from_file_location("pgn_runtime_notices_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Package:
        def __init__(self, name, version, notice_name=None):
            self.root = tmp_path / "private-package-location" / name
            self.version = version
            self.files = []
            if notice_name:
                item = Path(f"{name}.dist-info/licenses/{notice_name}")
                source = self.root / item
                source.parent.mkdir(parents=True)
                source.write_bytes(b"Synthetic upstream notice\r\nGPL and bootloader exception\r\nApache License 2.0\r\n")
                self.files.append(item)

        def locate_file(self, item):
            return self.root / item

    packages = {
        "chess": Package("chess", "1.11.2"),
        "PySide6-Essentials": Package("PySide6-Essentials", "6.11.2", "LicenseRef-Qt-Commercial.txt"),
        "shiboken6": Package("shiboken6", "6.11.2", "LicenseRef-Qt-Commercial.txt"),
        "pyinstaller": Package("pyinstaller", "6.22.3", "COPYING.txt"),
    }
    monkeypatch.setattr(module, "distribution", packages.__getitem__)
    python_root = tmp_path / "private-python-location"
    python_root.mkdir()
    (python_root / "LICENSE.txt").write_bytes(b"Synthetic Python license\r\n")
    monkeypatch.setattr(module.sys, "base_prefix", str(python_root))
    return module, packages


def test_runtime_notices_preserve_upstream_bytes_and_inventory_is_neutral(notice_collector, tmp_path):
    module, packages = notice_collector
    project = tmp_path / "project"
    module.collect_runtime_notices(project)
    notices = project / "licenses" / "runtime"
    package = packages["pyinstaller"]
    assert (notices / "pyinstaller" / "COPYING.txt").read_bytes() == package.locate_file(package.files[0]).read_bytes()
    assert (notices / "Python.txt").read_bytes() == b"Synthetic Python license\r\n"
    for name in ("PySide6-Essentials", "shiboken6"):
        assert (notices / name / "LicenseRef-Qt-Commercial.txt").is_file()
    assert (notices / "VERSIONS.txt").read_text(encoding="utf-8").splitlines() == [
        "Runtime and packaging versions for this build",
        f"CPython: {module.platform.python_version()}",
        f"Architecture: {module.struct.calcsize('P') * 8}-bit",
        f"SQLite: {module.sqlite3.sqlite_version}",
        "chess: 1.11.2",
        "PySide6-Essentials: 6.11.2",
        "shiboken6: 6.11.2",
        "pyinstaller: 6.22.3",
    ]
    assert not (project / ".build").exists()


@pytest.mark.parametrize("missing", ["metadata-entry", "source-file"])
def test_missing_pyinstaller_notice_fails_even_with_old_destination(notice_collector, tmp_path, missing):
    module, packages = notice_collector
    package = packages["pyinstaller"]
    if missing == "metadata-entry":
        package.files.clear()
    else:
        package.locate_file(package.files[0]).unlink()
    project = tmp_path / "project"
    stale = project / "licenses" / "runtime" / "pyinstaller" / "COPYING.txt"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"Old notice must not hide missing upstream material.")

    with pytest.raises(RuntimeError, match="PyInstaller COPYING.txt is missing"):
        module.collect_runtime_notices(project)

    assert stale.read_bytes() == b"Old notice must not hide missing upstream material."
    assert not (stale.parent.parent / "VERSIONS.txt").exists()
