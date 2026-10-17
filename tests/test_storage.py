import itertools
import os
import sqlite3

import pytest

from pgn_player.storage import APPLICATION_ID, DEFAULTS, NOTATION_FONT_SIZES, SettingsStore, StorageError


def test_fresh_store_has_defaults_and_no_private_state(tmp_path):
    with SettingsStore(tmp_path / "new.sqlite3") as store:
        assert store.get_settings() == DEFAULTS
        assert store.get_settings()["notation_font_size"] == 12
        assert store.get_settings()["notation_visible"] is True
        assert store.get_settings()["board_coordinates_visible"] is True
        assert store.recent_files() == []
        assert store.load_resume(tmp_path / "game.pgn", "hash") is None
        assert store.get("window_geometry") is None


def test_loop_defaults_off_for_existing_profiles_and_persists_without_reset(tmp_path):
    db_path = tmp_path / "existing.sqlite3"
    with SettingsStore(db_path) as store:
        store.set("continue_next", False)
        store.set("delay_seconds", 3.5)
        store.set("theme", "dark")
    with sqlite3.connect(db_path) as db:
        previous_rows = db.execute("SELECT key,value FROM settings ORDER BY key").fetchall()

    with SettingsStore(db_path) as store:
        settings = store.get_settings()
        assert settings["loop"] is False
        assert settings["continue_next"] is False
        assert settings["delay_seconds"] == 3.5
        assert settings["theme"] == "dark"
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT key,value FROM settings ORDER BY key").fetchall() == previous_rows

    for enabled in (True, False):
        with SettingsStore(db_path) as store:
            store.set("loop", enabled)
        with SettingsStore(db_path) as store:
            assert store.get_settings()["loop"] is enabled
            assert store.get_settings()["continue_next"] is False
            assert store.get_settings()["delay_seconds"] == 3.5
            assert store.get_settings()["theme"] == "dark"


@pytest.mark.parametrize("key", ["notation_visible", "board_coordinates_visible"])
def test_visibility_defaults_visible_and_preserves_other_preferences(tmp_path, key):
    db_path = tmp_path / "existing.sqlite3"
    with SettingsStore(db_path) as store:
        store.set("theme", "dark")
        store.set("notation_font_size", 16)
    with sqlite3.connect(db_path) as db:
        previous_rows = db.execute("SELECT key,value FROM settings ORDER BY key").fetchall()
    with SettingsStore(db_path) as store:
        assert store.get_settings()[key] is True
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT key,value FROM settings ORDER BY key").fetchall() == previous_rows

    for visible in (False, True):
        with SettingsStore(db_path) as store:
            store.set(key, visible)
        with SettingsStore(db_path) as store:
            settings = store.get_settings()
            assert settings[key] is visible
            assert settings["theme"] == "dark"
            assert settings["notation_font_size"] == 16
    with SettingsStore(tmp_path / "other-edition.sqlite3") as other:
        assert other.get_settings() == DEFAULTS


@pytest.mark.parametrize("encoded", ['"false"', "0", "null"])
@pytest.mark.parametrize("key", ["notation_visible", "board_coordinates_visible"])
def test_invalid_saved_visibility_falls_back_without_erasing(tmp_path, encoded, key):
    db_path = tmp_path / "invalid-visibility.sqlite3"
    with SettingsStore(db_path):
        pass
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO settings(key,value) VALUES(?,?)", (key, encoded))
    with SettingsStore(db_path) as store:
        assert store.get_settings()[key] is True
        with pytest.raises(ValueError):
            store.set(key, 0)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[0] == encoded


def test_notation_font_default_and_saved_sizes_preserve_existing_preferences(tmp_path):
    db_path = tmp_path / "existing.sqlite3"
    with SettingsStore(db_path) as store:
        store.set("theme", "dark")
        store.set("loop", True)
    with sqlite3.connect(db_path) as db:
        previous_rows = db.execute("SELECT key,value FROM settings ORDER BY key").fetchall()
    with SettingsStore(db_path) as store:
        assert store.get_settings()["notation_font_size"] == 12
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT key,value FROM settings ORDER BY key").fetchall() == previous_rows

    for size in NOTATION_FONT_SIZES:
        with SettingsStore(db_path) as store:
            store.set("notation_font_size", size)
        with SettingsStore(db_path) as store:
            assert store.get_settings()["notation_font_size"] == size
            assert store.get_settings()["theme"] == "dark"
            assert store.get_settings()["loop"] is True


@pytest.mark.parametrize("encoded", ["11", '"18"', "true", "14.0", "null"])
def test_invalid_saved_notation_size_defaults_to_12_without_erasing(tmp_path, encoded):
    db_path = tmp_path / "invalid-size.sqlite3"
    with SettingsStore(db_path):
        pass
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO settings(key,value) VALUES('notation_font_size',?)", (encoded,))
    with SettingsStore(db_path) as store:
        assert store.get_settings()["notation_font_size"] == 12
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT value FROM settings WHERE key='notation_font_size'").fetchone()[0] == encoded


def test_preferences_geometry_and_resume_survive_reopen(tmp_path):
    db = tmp_path / "state.sqlite3"
    pgn = tmp_path / "study.pgn"
    pgn.write_text("1. e4 e5 *", encoding="utf-8")
    with SettingsStore(db) as store:
        store.set("theme", "dark")
        store.set("delay_seconds", 3.5)
        store.set("window_geometry", "AAABAA==")
        store.set("panel_sizes", [650, 450])
        store.save_resume(pgn, {"size": 10, "digest": "abc"}, 7, [0, 0, 1, 0], "black")
    with SettingsStore(db) as store:
        assert store.get_settings()["theme"] == "dark"
        assert store.get_settings()["delay_seconds"] == 3.5
        assert store.get("window_geometry") == "AAABAA=="
        assert store.get("panel_sizes") == [650, 450]
        saved = store.load_resume(pgn, {"digest": "abc", "size": 10})
        assert saved["game_index"] == 7
        assert saved["variation_path"] == [0, 0, 1, 0]
        assert saved["orientation"] == "black"
    assert pgn.read_text(encoding="utf-8") == "1. e4 e5 *"


def test_changed_file_does_not_restore_or_delete_old_resume(tmp_path):
    with SettingsStore(tmp_path / "state.sqlite3") as store:
        pgn = tmp_path / "games.pgn"
        store.save_resume(pgn, "old", 10, [0, 1], "white")
        assert store.load_resume(pgn, "new") is None
        assert store.load_resume(pgn, "old")["game_index"] == 10


def test_recent_files_deduplicate_order_bound_and_preserve_source(tmp_path, monkeypatch):
    ticks = itertools.count(100)
    monkeypatch.setattr("pgn_player.storage.time.time", lambda: next(ticks))
    with SettingsStore(tmp_path / "state.sqlite3") as store:
        for index in range(23):
            store.add_recent(tmp_path / f"game{index}.pgn")
        pgn = tmp_path / "game4.pgn"
        pgn.write_text("*", encoding="utf-8")
        store.save_resume(pgn, "same", 0, [], "white")
        store.add_recent(pgn)
        store.add_recent(pgn)
        recent = store.recent_files()
        assert len(recent) == 10
        assert recent[0]["path"] == str(pgn.resolve())
        assert recent[0]["name"] == "game4.pgn"
        assert [entry["name"] for entry in recent] == ["game4.pgn"] + [f"game{index}.pgn" for index in range(22, 13, -1)]
        assert store._db.execute("SELECT COUNT(*) FROM recent_files").fetchone()[0] == 10
        store.remove_recent(pgn)
        assert len(store.recent_files()) == 9
        assert store.load_resume(pgn, "same") is not None
        assert pgn.read_text(encoding="utf-8") == "*"


def test_opening_existing_twenty_entry_history_prunes_to_newest_ten_only(tmp_path):
    db_path = tmp_path / "existing.sqlite3"
    source = tmp_path / "game0.pgn"
    with SettingsStore(db_path) as store:
        store.set("theme", "dark")
        store.set("last_file", str(source))
        store.save_resume(source, "fingerprint", 3, [0, 1], "black")
    with sqlite3.connect(db_path) as db:
        settings = db.execute("SELECT * FROM settings ORDER BY key").fetchall()
        resume = db.execute("SELECT * FROM resume_positions").fetchall()
        schema = db.execute("PRAGMA user_version").fetchone()[0]
        db.executemany("INSERT INTO recent_files(file_key,path,name,last_opened) VALUES(?,?,?,?)", [
            (os.path.normcase(str(tmp_path / f"game{index}.pgn")), str(tmp_path / f"game{index}.pgn"),
             f"game{index}.pgn", float(index)) for index in range(20)
        ])
    with SettingsStore(db_path) as store:
        assert [entry["name"] for entry in store.recent_files()] == [f"game{index}.pgn" for index in range(19, 9, -1)]
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM recent_files").fetchone()[0] == 10
        assert db.execute("SELECT * FROM settings ORDER BY key").fetchall() == settings
        assert db.execute("SELECT * FROM resume_positions").fetchall() == resume
        assert db.execute("PRAGMA user_version").fetchone()[0] == schema
    assert not list(tmp_path.glob("*.bak"))


def test_existing_overflow_history_opens_when_pruning_is_write_locked(tmp_path):
    db_path = tmp_path / "existing-locked.sqlite3"
    with SettingsStore(db_path):
        pass
    with sqlite3.connect(db_path) as db:
        db.executemany("INSERT INTO recent_files(file_key,path,name,last_opened) VALUES(?,?,?,?)", [
            (f"file-{index}", str(tmp_path / f"game{index}.pgn"), f"game{index}.pgn", float(index))
            for index in range(20)
        ])
    locker = sqlite3.connect(db_path)
    try:
        locker.execute("BEGIN IMMEDIATE")
        with SettingsStore(db_path, timeout=.01) as store:
            assert [entry["name"] for entry in store.recent_files()] == [f"game{index}.pgn" for index in range(19, 9, -1)]
            assert store._db.execute("SELECT COUNT(*) FROM recent_files").fetchone()[0] == 20
    finally:
        locker.rollback()
        locker.close()
    with SettingsStore(db_path) as store:
        assert store._db.execute("SELECT COUNT(*) FROM recent_files").fetchone()[0] == 10


def test_clear_recent_files_persists_and_preserves_pgn_cache_preferences_and_resume(tmp_path):
    from pgn_player.pgn_loader import open_pgn

    source = tmp_path / "study.pgn"
    source.write_text('[Event "Study"]\n\n1. e4 e5 *\n', encoding="utf-8")
    cache = tmp_path / "pgn-index-cache.sqlite3"
    collection = open_pgn(source, cache_path=cache)
    source_bytes, cache_bytes = source.read_bytes(), cache.read_bytes()
    db_path = tmp_path / "state.sqlite3"
    with SettingsStore(db_path) as store:
        store.set("theme", "dark")
        store.set("reopen_last", True)
        store.set("last_file", str(source))
        store.save_resume(source, collection.fingerprint, 0, [0], "black")
        store.add_recent(source)
        store.add_recent(tmp_path / "another.pgn")
        preferences = store.get_settings()
        resume = store.load_resume(source, collection.fingerprint)
        store.clear_recent_files()
        assert store.recent_files() == []
        assert store._db.execute("SELECT COUNT(*) FROM recent_files").fetchone()[0] == 0
    with SettingsStore(db_path) as store:
        assert store.recent_files() == []
        assert store.get_settings() == preferences
        assert store.get("last_file") == str(source)
        assert store.load_resume(source, collection.fingerprint) == resume
        store.clear_recent_files()  # Clearing an empty list is harmless.
    assert source.read_bytes() == source_bytes
    assert cache.read_bytes() == cache_bytes


def test_clear_recent_files_rolls_back_if_a_delete_fails(tmp_path):
    with SettingsStore(tmp_path / "state.sqlite3") as store:
        for index in range(3):
            store.add_recent(tmp_path / f"game{index}.pgn")
        recent = store.recent_files()
        with store._db:
            store._db.execute("CREATE TRIGGER reject_recent_delete BEFORE DELETE ON recent_files "
                              "WHEN old.name='game1.pgn' BEGIN SELECT RAISE(FAIL, 'delete blocked'); END")
        with pytest.raises(StorageError, match="delete blocked"):
            store.clear_recent_files()
        assert store.recent_files() == recent
        assert store._db.execute("SELECT COUNT(*) FROM recent_files").fetchone()[0] == 3


def test_clear_recent_files_does_not_affect_other_edition(tmp_path, monkeypatch):
    from pgn_player.paths import resolve_paths

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    portable = resolve_paths("portable", app_dir=tmp_path / "application")
    installed = resolve_paths("installed", app_dir=tmp_path / "application")
    source = tmp_path / "shared.pgn"
    for paths in (portable, installed):
        with SettingsStore(paths.db_path) as store:
            store.add_recent(source)
            store.save_resume(source, "same", 0, [0], "white")
    with SettingsStore(portable.db_path) as store:
        store.clear_recent_files()
        assert store.load_resume(source, "same") is not None
    with SettingsStore(portable.db_path) as store:
        assert store.recent_files() == []
    with SettingsStore(installed.db_path) as store:
        assert [entry["path"] for entry in store.recent_files()] == [str(source)]
        assert store.load_resume(source, "same") is not None


@pytest.mark.parametrize("key,value", [
    ("theme", "neon"), ("delay_seconds", -1), ("delay_seconds", True),
    ("delay_seconds", float("inf")), ("between_games_seconds", -0.1),
    ("continue_next", "false"), ("loop", "false"), ("loop", 1),
    ("notation_font_size", 11), ("notation_font_size", "16"),
    ("notation_font_size", 16.0), ("notation_font_size", True),
    ("guess_color", "red"), ("orientation", []),
])
def test_invalid_preference_is_rejected_without_replacing_value(tmp_path, key, value):
    with SettingsStore(tmp_path / "state.sqlite3") as store:
        with pytest.raises(ValueError):
            store.set(key, value)
        assert store.get_settings()[key] == DEFAULTS[key]


def test_invalid_saved_json_and_preferences_fall_back_without_erasing(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    with SettingsStore(db_path):
        pass
    with sqlite3.connect(db_path) as db:
        db.executemany("INSERT INTO settings(key,value) VALUES(?,?)", [
            ("theme", '"neon"'), ("delay_seconds", '"slow"'), ("continue_next", "broken-json"),
        ])
    with SettingsStore(db_path) as store:
        assert store.get_settings() == DEFAULTS
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT value FROM settings WHERE key='theme'").fetchone()[0] == '"neon"'


def test_database_lock_fails_cleanly_without_overwriting_data(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    with SettingsStore(db_path, timeout=0.01) as store:
        store.set("theme", "dark")
        locker = sqlite3.connect(db_path)
        try:
            locker.execute("BEGIN EXCLUSIVE")
            with pytest.raises(StorageError, match="locked"):
                store.set("theme", "light")
        finally:
            locker.rollback()
            locker.close()
        assert store.get("theme") == "dark"


def test_corrupt_database_is_never_recreated(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    original = b"This is not SQLite data; preserve it."
    db_path.write_bytes(original)
    with pytest.raises(StorageError):
        SettingsStore(db_path)
    assert db_path.read_bytes() == original


def test_unknown_or_future_database_is_preserved(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE personal_notes (note TEXT)")
        db.execute("INSERT INTO personal_notes VALUES('preserve')")
    with pytest.raises(StorageError, match="does not belong"):
        SettingsStore(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT note FROM personal_notes").fetchone()[0] == "preserve"
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute("PRAGMA user_version=999")
    with pytest.raises(StorageError, match="newer"):
        SettingsStore(db_path)


def test_existing_empty_database_is_backed_up_before_schema_creation(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA user_version=0")
    with SettingsStore(db_path) as store:
        store.set("theme", "dark")
    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 0
        assert backup.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []


def test_sql_content_is_stored_as_data(tmp_path):
    with SettingsStore(tmp_path / "state.sqlite3") as store:
        key = "name'); DROP TABLE settings; --"
        store.set(key, {"text": "quotes ' and unicode ♞"})
        assert store.get(key) == {"text": "quotes ' and unicode ♞"}
        assert store.get_settings() == DEFAULTS
