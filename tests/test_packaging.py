"""Exercise publication with synthetic data so no real user profile is touched."""

import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture
def publisher(tmp_path):
    spec = importlib.util.spec_from_file_location("pgn_packaging_test", PROJECT / "scripts" / "package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    payload = tmp_path / ".build" / "payload" / "PGN Player"
    payload.mkdir(parents=True)
    (payload / "PGN Player.exe").write_bytes(b"synthetic application payload")
    (payload / "_internal").mkdir()
    (payload / "_internal" / "runtime.dll").write_bytes(b"synthetic runtime")
    for directory in module.SOURCE_DIRS:
        (tmp_path / directory).mkdir(exist_ok=True)
    for filename in module.SOURCE_FILES:
        (tmp_path / filename).write_text("Neutral project material.\n", encoding="utf-8")
    (tmp_path / "docs" / "SHORTCUTS.md").write_text("Space: play or pause.\n", encoding="utf-8")
    for edition in ("portable", "installed"):
        (tmp_path / "packaging" / f"{edition}.json").write_text(json.dumps({"edition": edition}), encoding="utf-8")
    (tmp_path / "src" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    return module


def _private_archive_files(archive):
    private_directories = {"data", ".local-data", ".venv", ".git", "__pycache__", ".pytest_cache"}
    private_suffixes = {".db", ".sqlite", ".sqlite3", ".log", ".bak", ".backup", ".pyc", ".pyo"}
    return [name for name in archive.namelist() if not name.endswith("/") and (
        any(part.casefold() in private_directories for part in Path(name).parts)
        or Path(name).suffix.casefold() in private_suffixes
        or name.casefold().endswith(("-wal", "-shm", "-journal"))
    )]


def test_publish_preserves_live_data_and_archives_only_clean_inputs(publisher):
    root = publisher.ROOT
    portable = root / "dist" / "portable" / "PGN Player"
    data = portable / "data"
    data.mkdir(parents=True)
    secret = b"Synthetic saved preference that must stay local."
    (data / "pgn-player.sqlite3").write_bytes(secret)
    (data / "pgn-player.sqlite3-wal").write_bytes(secret)
    (portable / "my-private-study.pgn").write_bytes(secret)
    (portable / "old-runtime.dll").write_bytes(b"obsolete runtime")
    (portable / "application-files.json").write_text(json.dumps(["old-runtime.dll"]), encoding="utf-8")
    publisher.publish()
    assert (data / "pgn-player.sqlite3").read_bytes() == secret
    assert (data / "pgn-player.sqlite3-wal").read_bytes() == secret
    assert (portable / "my-private-study.pgn").read_bytes() == secret
    assert not (portable / "old-runtime.dll").exists()
    assert json.loads((portable / "edition.json").read_text())["edition"] == "portable"
    with zipfile.ZipFile(root / "dist" / "PGN Player Portable.zip") as archive:
        assert "PGN Player/PGN Player.exe" in archive.namelist()
        assert "PGN Player/data/" in archive.namelist()
        assert "PGN Player/my-private-study.pgn" not in archive.namelist()
        assert not _private_archive_files(archive)
        assert json.loads(archive.read("PGN Player/edition.json")) == {"edition": "portable"}
        assert all(secret not in archive.read(name) for name in archive.namelist())


def test_source_archive_excludes_local_storage_and_keeps_generated_fixtures(publisher):
    root = publisher.ROOT
    for name in ("src/data/private.pgn", "src/.local-data/settings.json", "src/__pycache__/module.pyc",
                 "tests/diagnostic.log", "tests/private.sqlite3", "scripts/recovery.bak"):
        private = root / name
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text("Synthetic private content", encoding="utf-8")
    fixture = root / "tests" / "fixtures" / "generated.pgn"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("1. e4 e5 *", encoding="utf-8")
    publisher.publish()
    with zipfile.ZipFile(root / "dist" / "PGN Player Source.zip") as archive:
        assert not _private_archive_files(archive)
        assert "PGN Player/src/example.py" in archive.namelist()
        assert "PGN Player/tests/fixtures/generated.pgn" in archive.namelist()
        assert all(b"Synthetic private content" not in archive.read(name) for name in archive.namelist())


@pytest.mark.parametrize("private_name", ["data/profile.json", "settings.sqlite3", "settings.sqlite3-wal", "session.log"])
def test_private_runtime_payload_is_rejected_before_publication(publisher, private_name):
    payload = publisher.ROOT / ".build" / "payload" / "PGN Player"
    private = payload / private_name
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text("Synthetic private content", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Private runtime file"):
        publisher.publish()
    assert not (publisher.ROOT / "dist" / "PGN Player Portable.zip").exists()
    assert private.exists()


@pytest.mark.parametrize("entry", ["../../outside.txt", "data/pgn-player.sqlite3"])
def test_unsafe_previous_manifest_cannot_remove_data_or_escape_portable(publisher, entry):
    root = publisher.ROOT
    portable = root / "dist" / "portable" / "PGN Player"
    (portable / "data").mkdir(parents=True)
    (portable / "data" / "pgn-player.sqlite3").write_bytes(b"preserve local data")
    (root / "dist" / "outside.txt").write_text("preserve unrelated file", encoding="utf-8")
    (portable / "application-files.json").write_text(json.dumps([entry]), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Unsafe application manifest"):
        publisher.publish()
    assert (portable / "data" / "pgn-player.sqlite3").read_bytes() == b"preserve local data"
    assert (root / "dist" / "outside.txt").read_text(encoding="utf-8") == "preserve unrelated file"


def test_repeated_publication_reuses_canonical_location(publisher):
    publisher.publish()
    publisher.publish()
    dist = publisher.ROOT / "dist"
    assert sorted(path.name for path in (dist / "portable").iterdir()) == ["PGN Player"]
    assert sorted(path.name for path in dist.glob("*.zip")) == ["PGN Player Portable.zip", "PGN Player Source.zip"]
    assert len(list(dist.rglob("PGN Player.exe"))) == 1


def test_packaging_markers_and_installer_preserve_edition_data():
    assert json.loads((PROJECT / "packaging" / "portable.json").read_text()) == {"edition": "portable"}
    assert json.loads((PROJECT / "packaging" / "installed.json").read_text()) == {"edition": "installed"}
    installer = (PROJECT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    active = "\n".join(line.strip() for line in installer.splitlines() if not line.strip().startswith(";"))
    assert "CloseApplications=no" in active
    assert "RestartApplications=no" in active
    assert "AppMutex=PGNPlayer.Installed" in active
    assert "[Run]" not in active
    assert "[UninstallDelete]" not in active
    assert 'DestName: "edition.json"' in active
    assert "packaging\\installed.json" in active
    assert "{localappdata}\\PGN Player\\Installed\\data" not in active
    # Inno invokes this hook before changing the installation directory. The
    # guard must retain both marker validation and rejection of unknown files.
    assert "function PrepareToInstall" in active
    assert "if FileExists(Marker) then" in active
    assert "if Config <> '{\"edition\":\"installed\"}' then" in active
    assert "if HasContents then" in active


@pytest.mark.parametrize("filename", ["PGN Player Portable.zip", "PGN Player Source.zip"])
def test_built_archives_have_no_private_runtime_files(filename):
    archive_path = PROJECT / "dist" / filename
    if not archive_path.exists():
        pytest.skip("Release archive has not been built yet.")
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        assert not _private_archive_files(archive)
        if filename == "PGN Player Portable.zip":
            assert "PGN Player/PGN Player.exe" in archive.namelist()
            assert json.loads(archive.read("PGN Player/edition.json")) == {"edition": "portable"}
            # Pinned Qt 6.11 uses Windows system ICU exports (for example
            # ucnv_open). A PATH-sourced third-party ICU can expose only versioned
            # symbols and break QtGui imports despite all Qt files matching.
            assert not {"icuuc.dll", "icudt78.dll"}.intersection(
                Path(name).name.casefold() for name in archive.namelist()
            ), "Windows system ICU must not be shadowed by third-party bundled DLLs."
