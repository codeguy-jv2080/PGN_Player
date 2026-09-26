import json
import sys

import pytest

from pgn_player.paths import StoragePathError, resolve_paths
from pgn_player.storage import DEFAULTS, SettingsStore


def test_portable_installed_and_development_are_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    app_dir = tmp_path / "application"
    portable = resolve_paths("portable", app_dir=app_dir)
    installed = resolve_paths("installed", app_dir=app_dir)
    development = resolve_paths("development", app_dir=app_dir)
    assert len({portable.db_path, installed.db_path, development.db_path}) == 3
    assert portable.data_dir == app_dir / "data"
    assert installed.data_dir == tmp_path / "local-app-data" / "PGN Player" / "Installed" / "data"
    assert development.data_dir == app_dir / ".local-data"
    source = tmp_path / "shared-game.pgn"
    with SettingsStore(portable.db_path) as store:
        store.set("theme", "dark")
        store.add_recent(source)
        store.save_resume(source, "digest", 4, [0, 1], "black")
    for other in (installed, development):
        with SettingsStore(other.db_path) as store:
            assert store.get_settings() == DEFAULTS
            assert store.recent_files() == []
            assert store.load_resume(source, "digest") is None
            store.set("delay_seconds", 7)
    with SettingsStore(portable.db_path) as store:
        assert store.get("theme") == "dark"
        assert store.get_settings()["delay_seconds"] == 2.0
        assert store.load_resume(source, "digest")["game_index"] == 4


def test_frozen_build_requires_valid_edition_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(StoragePathError, match="edition"):
        resolve_paths(app_dir=tmp_path)
    for payload in ("bad json", '"portable"', '{"edition":"development"}', '{"edition":"unknown"}'):
        (tmp_path / "edition.json").write_text(payload, encoding="utf-8")
        with pytest.raises(StoragePathError, match="edition"):
            resolve_paths(app_dir=tmp_path)
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / ".local-data").exists()


def test_frozen_portable_uses_executable_folder_and_rejects_override(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "PGN Player.exe"))
    (tmp_path / "edition.json").write_text(json.dumps({"edition": "portable"}), encoding="utf-8")
    assert resolve_paths().data_dir == tmp_path / "data"
    with pytest.raises(StoragePathError, match="conflicts"):
        resolve_paths("installed")


def test_installed_missing_environment_never_falls_back_to_portable(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(StoragePathError, match="LOCALAPPDATA"):
        resolve_paths("installed", app_dir=tmp_path)
    assert not (tmp_path / "data").exists()


def test_unwritable_target_does_not_choose_another_directory(tmp_path):
    blocked = tmp_path / "data"
    blocked.write_text("existing file must survive", encoding="utf-8")
    with pytest.raises(StoragePathError, match="cannot write"):
        resolve_paths("portable", app_dir=tmp_path)
    assert blocked.read_text(encoding="utf-8") == "existing file must survive"
    assert not (tmp_path / ".local-data").exists()


def test_explicit_test_override_is_confined_to_supplied_directory(tmp_path):
    selected = tmp_path / "test-state"
    paths = resolve_paths(app_dir=tmp_path / "project", data_override=selected)
    assert paths.edition == "development"
    assert paths.data_dir == selected
    assert paths.db_path == selected / "pgn-player.sqlite3"
    assert not (tmp_path / "project").exists()
