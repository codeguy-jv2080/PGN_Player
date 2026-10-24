"""Sound is an opt-in preference stored independently for each app edition."""
import sqlite3

import pytest

from pgn_player.paths import resolve_paths
from pgn_player.storage import SettingsStore


def test_sound_defaults_off_without_rewriting_existing_preferences_and_is_edition_local(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    portable = resolve_paths("portable", app_dir=tmp_path / "application")
    installed = resolve_paths("installed", app_dir=tmp_path / "application")
    with SettingsStore(portable.db_path) as store:
        store.set("theme", "dark")
        store.set("notation_font_size", 16)
    with sqlite3.connect(portable.db_path) as db:
        before = db.execute("SELECT * FROM settings ORDER BY key").fetchall()
    with SettingsStore(portable.db_path) as store:
        assert store.get_settings()["sound_enabled"] is False
    with sqlite3.connect(portable.db_path) as db:
        assert db.execute("SELECT * FROM settings ORDER BY key").fetchall() == before
    with SettingsStore(portable.db_path) as store:
        store.set("sound_enabled", True)
    with SettingsStore(installed.db_path) as store:
        assert store.get_settings()["sound_enabled"] is False
        store.set("sound_enabled", False)
    with SettingsStore(portable.db_path) as store:
        assert store.get_settings()["sound_enabled"] is True
        assert store.get_settings()["theme"] == "dark"
        assert store.get_settings()["notation_font_size"] == 16
        store.set("sound_enabled", False)
    with SettingsStore(portable.db_path) as store:
        assert store.get_settings()["sound_enabled"] is False


@pytest.mark.parametrize("encoded", ['"true"', "1", "null"])
def test_invalid_sound_preferences_fall_back_without_erasing_saved_values(tmp_path, encoded):
    db_path = tmp_path / "preferences.sqlite3"
    with SettingsStore(db_path):
        pass
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO settings(key,value) VALUES('sound_enabled',?)", (encoded,))
    with SettingsStore(db_path) as store:
        assert store.get_settings()["sound_enabled"] is False
        for value in ("true", 1, None):
            with pytest.raises(ValueError):
                store.set("sound_enabled", value)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT value FROM settings WHERE key='sound_enabled'").fetchone()[0] == encoded
