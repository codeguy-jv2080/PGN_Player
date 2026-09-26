import sqlite3

import pytest

from pgn_player.storage import APPLICATION_ID, DEFAULTS, SettingsStore, StorageError


def test_fresh_store_has_defaults_and_no_private_state(tmp_path):
    with SettingsStore(tmp_path / "new.sqlite3") as store:
        assert store.get_settings() == DEFAULTS
        assert store.recent_files() == []
        assert store.load_resume(tmp_path / "game.pgn", "hash") is None
        assert store.get("window_geometry") is None


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


def test_recent_files_deduplicate_order_bound_and_preserve_source(tmp_path):
    with SettingsStore(tmp_path / "state.sqlite3") as store:
        for index in range(23):
            store.add_recent(tmp_path / f"game{index}.pgn")
        pgn = tmp_path / "game4.pgn"
        pgn.write_text("*", encoding="utf-8")
        store.save_resume(pgn, "same", 0, [], "white")
        store.add_recent(pgn)
        store.add_recent(pgn)
        recent = store.recent_files()
        assert len(recent) == 20
        assert recent[0]["path"] == str(pgn.resolve())
        assert recent[0]["name"] == "game4.pgn"
        store.remove_recent(pgn)
        assert len(store.recent_files()) == 19
        assert store.load_resume(pgn, "same") is not None
        assert pgn.read_text(encoding="utf-8") == "*"


@pytest.mark.parametrize("key,value", [
    ("theme", "neon"), ("delay_seconds", -1), ("delay_seconds", True),
    ("delay_seconds", float("inf")), ("between_games_seconds", -0.1),
    ("continue_next", "false"), ("guess_color", "red"), ("orientation", []),
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
