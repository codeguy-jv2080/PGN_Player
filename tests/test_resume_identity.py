"""Saved-game identities preserve existing profiles through an additive migration."""

import json
import os
import sqlite3

import pytest

import pgn_player.storage as storage_module
from pgn_player.storage import APPLICATION_ID, DEFAULTS, SCHEMA_VERSION, SettingsStore, StorageError


DIGEST = "0123456789abcdef" * 4
FINGERPRINT = {"digest": "collection fingerprint", "version": 1}
RESUME_COLUMNS = "file_key,path,fingerprint,game_index,variation_path,orientation,updated_at"


def _legacy_database(path, source):
    """Create the exact version-one tables without opening current storage."""
    db = sqlite3.connect(path)
    try:
        db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute(
            "CREATE TABLE recent_files (file_key TEXT PRIMARY KEY, path TEXT NOT NULL, "
            "name TEXT NOT NULL, last_opened REAL NOT NULL)"
        )
        db.execute(
            "CREATE TABLE resume_positions (file_key TEXT PRIMARY KEY, path TEXT NOT NULL, "
            "fingerprint TEXT NOT NULL, game_index INTEGER NOT NULL CHECK(game_index >= 0), "
            "variation_path TEXT NOT NULL, orientation TEXT NOT NULL, updated_at REAL NOT NULL)"
        )
        canonical = str(source.resolve())
        key = os.path.normcase(canonical)
        db.executemany("INSERT INTO settings VALUES(?,?)", [
            ("theme", '"dark"'), ("notation_font_size", "18"),
            ("loop", "true"), ("window_geometry", '"saved geometry"'),
        ])
        db.execute("INSERT INTO recent_files VALUES(?,?,?,?)", (key, canonical, source.name, 1234.5))
        db.execute("INSERT INTO resume_positions VALUES(?,?,?,?,?,?,?)", (
            key, canonical, json.dumps(FINGERPRINT, separators=(",", ":"), sort_keys=True),
            7, "[0,1,0]", "black", 2345.6,
        ))
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute("PRAGMA user_version=1")
        db.commit()
    finally:
        db.close()


def _rows(path):
    db = sqlite3.connect(path)
    try:
        return {
            "settings": db.execute("SELECT * FROM settings ORDER BY key").fetchall(),
            "recent": db.execute("SELECT * FROM recent_files ORDER BY file_key").fetchall(),
            "resume": db.execute(f"SELECT {RESUME_COLUMNS} FROM resume_positions ORDER BY file_key").fetchall(),
            "version": db.execute("PRAGMA user_version").fetchone()[0],
            "columns": [row[1] for row in db.execute("PRAGMA table_info(resume_positions)")],
        }
    finally:
        db.close()


def test_fresh_profile_has_nullable_identity_and_no_user_state(tmp_path):
    path = tmp_path / "fresh.sqlite3"
    with SettingsStore(path) as store:
        assert store.get_settings() == DEFAULTS
        assert store.recent_files() == []
        assert store.load_resume(tmp_path / "game.pgn", FINGERPRINT) is None
        assert store._db.execute("SELECT COUNT(*) FROM resume_positions").fetchone()[0] == 0
        column = next(row for row in store._db.execute("PRAGMA table_info(resume_positions)") if row[1] == "game_digest")
        assert column[2] == "TEXT" and column[3] == 0
        assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 2
    assert list(tmp_path.glob("*.bak")) == []


def test_version_one_migration_preserves_every_row_and_creates_one_backup(tmp_path):
    path, source = tmp_path / "state.sqlite3", tmp_path / "study.pgn"
    _legacy_database(path, source)
    original = _rows(path)
    with SettingsStore(path) as store:
        assert store.get("theme") == "dark"
        assert store.get("notation_font_size") == 18
        assert store.get("window_geometry") == "saved geometry"
        resume = store.load_resume(source, FINGERPRINT)
        assert resume["game_index"] == 7 and resume["variation_path"] == [0, 1, 0]
        assert resume["orientation"] == "black" and resume["game_digest"] is None
    migrated = _rows(path)
    for key in ("settings", "recent", "resume"):
        assert migrated[key] == original[key]
    assert migrated["version"] == 2
    assert migrated["columns"] == original["columns"] + ["game_digest"]
    backups = list(tmp_path.glob("state.sqlite3.before-v2-*.bak"))
    assert len(backups) == 1
    assert _rows(backups[0]) == original
    with SettingsStore(path) as store:
        store.save_resume(source, FINGERPRINT, 7, [0, 1, 0], "black", game_digest=DIGEST)
    with SettingsStore(path) as store:
        assert store.load_resume(source, FINGERPRINT)["game_digest"] == DIGEST
    assert len(list(tmp_path.glob("*.bak"))) == 1
    assert _rows(path)["settings"] == original["settings"]
    assert _rows(path)["recent"] == original["recent"]


def test_migration_backup_includes_committed_wal_state(tmp_path):
    path, source = tmp_path / "state.sqlite3", tmp_path / "study.pgn"
    _legacy_database(path, source)
    writer = sqlite3.connect(path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("UPDATE settings SET value='16' WHERE key='notation_font_size'")
        writer.commit()
        original = _rows(path)
        with SettingsStore(path) as store:
            assert store.get("notation_font_size") == 16
            assert store.load_resume(source, FINGERPRINT)["game_digest"] is None
        backups = list(tmp_path.glob("*.bak"))
        assert len(backups) == 1 and _rows(backups[0]) == original
    finally:
        writer.close()


def test_failed_backup_leaves_version_one_data_untouched(tmp_path, monkeypatch):
    path, source = tmp_path / "state.sqlite3", tmp_path / "study.pgn"
    _legacy_database(path, source)
    original = path.read_bytes()

    def fail_backup(self, version):
        assert version == 1
        raise StorageError("Synthetic backup failure")

    monkeypatch.setattr(SettingsStore, "_backup_before_migration", fail_backup)
    with pytest.raises(StorageError, match="backup failure"):
        SettingsStore(path)
    assert path.read_bytes() == original
    assert _rows(path)["version"] == 1


def test_failed_migration_rolls_back_column_and_version(tmp_path, monkeypatch):
    path, source = tmp_path / "state.sqlite3", tmp_path / "study.pgn"
    _legacy_database(path, source)
    original = _rows(path)
    real_connect = sqlite3.connect

    class FailVersionUpdate(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA user_version=2":
                raise sqlite3.OperationalError("Synthetic migration failure")
            return super().execute(sql, *args, **kwargs)

    def failing_connect(*args, **kwargs):
        return real_connect(*args, factory=FailVersionUpdate, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(storage_module.sqlite3, "connect", failing_connect)
        with pytest.raises(StorageError, match="migration failure"):
            SettingsStore(path)
    assert _rows(path) == original
    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 1 and _rows(backups[0]) == original


def test_unsupported_version_one_schema_is_not_partially_migrated(tmp_path):
    path, source = tmp_path / "state.sqlite3", tmp_path / "study.pgn"
    _legacy_database(path, source)
    db = sqlite3.connect(path)
    try:
        db.execute("ALTER TABLE resume_positions RENAME TO preserved_records")
        db.execute("CREATE TABLE resume_positions (file_key TEXT PRIMARY KEY)")
        db.commit()
    finally:
        db.close()
    original = path.read_bytes()
    with pytest.raises(StorageError, match="unsupported resume_positions schema"):
        SettingsStore(path)
    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.bak")) == []


def test_resume_digest_round_trip_and_old_positional_api(tmp_path):
    path, source = tmp_path / "state.sqlite3", tmp_path / "study.pgn"
    with SettingsStore(path) as store:
        store.save_resume(source, FINGERPRINT, 3, [0, 1], "black", game_digest=DIGEST)
    with SettingsStore(path) as store:
        saved = store.load_resume(source, FINGERPRINT)
        assert saved["fingerprint"] == FINGERPRINT
        assert saved["game_digest"] == DIGEST
        assert saved["game_index"] == 3 and saved["variation_path"] == [0, 1]
        assert store.load_resume(source, {"digest": "changed"}) is None
        # An old caller must not carry a previous game's identity forward.
        store.save_resume(source, FINGERPRINT, 4, [], "white")
        assert store.load_resume(source, FINGERPRINT)["game_digest"] is None


@pytest.mark.parametrize("invalid", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "a" * 63 + "\n", 123, True, b"a" * 64, [], {}])
def test_invalid_digest_cannot_overwrite_valid_resume(tmp_path, invalid):
    source = tmp_path / "study.pgn"
    with SettingsStore(tmp_path / "state.sqlite3") as store:
        store.save_resume(source, FINGERPRINT, 2, [0], "white", game_digest=DIGEST)
        saved = store.load_resume(source, FINGERPRINT)
        with pytest.raises(ValueError, match="game digest"):
            store.save_resume(source, FINGERPRINT, 3, [1], "black", game_digest=invalid)
        assert store.load_resume(source, FINGERPRINT) == saved


@pytest.mark.parametrize("invalid", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "a" * 63 + "\n", 123, b"a" * 64])
def test_corrupt_saved_digest_is_rejected_without_deleting_row(tmp_path, invalid):
    path, source = tmp_path / "state.sqlite3", tmp_path / "study.pgn"
    with SettingsStore(path) as store:
        store.save_resume(source, FINGERPRINT, 2, [0], "white", game_digest=DIGEST)
        with store._db:
            store._db.execute("UPDATE resume_positions SET game_digest=?", (invalid,))
        original = tuple(store._db.execute("SELECT * FROM resume_positions").fetchone())
        assert store.load_resume(source, FINGERPRINT) is None
        assert tuple(store._db.execute("SELECT * FROM resume_positions").fetchone()) == original
