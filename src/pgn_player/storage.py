"""Small SQLite store for local preferences, recent PGNs, and resume positions.

PGN contents are never written here or changed by this module. Each application
edition passes its own database path. Unknown or damaged databases are rejected
and preserved rather than being reset.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Sequence


NOTATION_FONT_SIZES = (10, 12, 14, 16, 18)
DEFAULT_NOTATION_FONT_SIZE = 12

DEFAULTS: dict[str, Any] = {
    "theme": "light",
    "notation_font_size": DEFAULT_NOTATION_FONT_SIZE,
    "delay_seconds": 2.0,
    "between_games_seconds": 1.0,
    "continue_next": True,
    "loop": False,
    "include_variations": False,
    "orientation": "white",
    "reopen_last": False,
    "guess_color": "white",
    "guess_variations": False,
}

SCHEMA_VERSION = 1
APPLICATION_ID = 0x50474E50  # PGNP
RECENT_FILE_LIMIT = 20


class StorageError(RuntimeError):
    """A database operation failed; existing data has not been reset."""


def _valid_preference(key: str, value: Any) -> bool:
    if key == "notation_font_size":
        return type(value) is int and value in NOTATION_FONT_SIZES
    if key in {"continue_next", "loop", "include_variations", "reopen_last", "guess_variations"}:
        return type(value) is bool
    if key == "theme":
        return isinstance(value, str) and value in {"light", "dark"}
    if key == "orientation":
        return isinstance(value, str) and value in {"white", "black"}
    if key == "guess_color":
        return isinstance(value, str) and value in {"white", "black", "both"}
    if key in {"delay_seconds", "between_games_seconds"}:
        lower = 0.1 if key == "delay_seconds" else 0.0
        return type(value) in {int, float} and math.isfinite(value) and lower <= value <= 120.0
    return True


def _encode(value: Any) -> str:
    # Deterministic encoding also makes dictionary-based file fingerprints safe
    # to compare regardless of dictionary insertion order.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _file_identity(path: str | Path) -> tuple[str, str]:
    canonical = str(Path(path).expanduser().resolve())
    return os.path.normcase(canonical), canonical


class SettingsStore:
    """Main-thread local state store with atomic, parameterized writes.

    Invalid saved preferences fall back to application defaults without erasing
    their records. A changed file fingerprint simply makes its resume ineligible.
    ``timeout`` bounds the wait when another process has locked the database.
    """

    def __init__(self, db_path: str | Path, *, timeout: float = 1.5) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self._connection: sqlite3.Connection | None = None
        existed = self.db_path.exists() and self.db_path.stat().st_size > 0
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.db_path, timeout=timeout)
            self._connection.row_factory = sqlite3.Row
            self._initialize(existed)
        except (OSError, sqlite3.Error) as exc:
            self.close()
            raise StorageError(f"Cannot open local storage at {self.db_path}: {exc}") from exc
        except Exception:
            self.close()
            raise

    @property
    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StorageError("Local storage is closed.")
        return self._connection

    def _initialize(self, existed: bool) -> None:
        db = self._db
        integrity = db.execute("PRAGMA quick_check(1)").fetchone()[0]
        if integrity != "ok":
            raise StorageError(f"The local database is damaged and was preserved: {integrity}")
        application_id = db.execute("PRAGMA application_id").fetchone()[0]
        version = db.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if application_id not in {0, APPLICATION_ID} or (application_id == 0 and (version != 0 or tables)):
            raise StorageError("This database does not belong to PGN Player and was left unchanged.")
        if version > SCHEMA_VERSION:
            raise StorageError("This database was created by a newer PGN Player. Update the application to open it.")
        if version < SCHEMA_VERSION:
            if existed:
                self._backup_before_migration(version)
            try:
                db.execute("BEGIN IMMEDIATE")
                # Version zero is an empty, uninitialized database. Every future
                # migration must be added here and remain inside this transaction.
                if version == 0:
                    if tables:
                        raise StorageError("Unrecognized legacy schema was preserved without changes.")
                    for sql in (
                        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
                        "CREATE TABLE recent_files (file_key TEXT PRIMARY KEY, path TEXT NOT NULL, "
                        "name TEXT NOT NULL, last_opened REAL NOT NULL)",
                        "CREATE TABLE resume_positions (file_key TEXT PRIMARY KEY, path TEXT NOT NULL, "
                        "fingerprint TEXT NOT NULL, game_index INTEGER NOT NULL CHECK(game_index >= 0), "
                        "variation_path TEXT NOT NULL, orientation TEXT NOT NULL, updated_at REAL NOT NULL)",
                    ):
                        db.execute(sql)
                db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                db.commit()
            except Exception:
                db.rollback()
                raise
        self._validate_schema()

    def _backup_before_migration(self, version: int) -> None:
        # Online SQLite backup includes committed WAL state, unlike copying the
        # .sqlite3 file. Backups stay beside this edition's own database.
        backup_path = self.db_path.with_name(f"{self.db_path.name}.before-v{version + 1}-{time.time_ns()}.bak")
        target = None
        try:
            target = sqlite3.connect(backup_path)
            self._db.backup(target)
        except (OSError, sqlite3.Error) as exc:
            raise StorageError(f"Cannot back up local data before migration: {exc}") from exc
        finally:
            if target is not None:
                target.close()

    def _validate_schema(self) -> None:
        expected = {
            "settings": {"key", "value"},
            "recent_files": {"file_key", "path", "name", "last_opened"},
            "resume_positions": {"file_key", "path", "fingerprint", "game_index", "variation_path", "orientation", "updated_at"},
        }
        for table, columns in expected.items():
            actual = {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}
            if not columns.issubset(actual):
                raise StorageError(f"Local storage has an unsupported {table} schema and was preserved.")

    def get(self, key: str, default: Any = None) -> Any:
        try:
            row = self._db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if row is None:
                return default
            try:
                return json.loads(row["value"])
            except (ValueError, TypeError):
                return default
        except sqlite3.Error as exc:
            raise StorageError(f"Cannot read local preferences: {exc}") from exc

    def set(self, key: str, value: Any) -> None:
        if not isinstance(key, str) or not key:
            raise ValueError("A setting must have a non-empty string key.")
        if not _valid_preference(key, value):
            raise ValueError(f"Invalid value for preference {key}.")
        encoded = _encode(value)
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, encoded),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Cannot save local preferences: {exc}") from exc

    def get_settings(self) -> dict[str, Any]:
        result = dict(DEFAULTS)
        for key in result:
            value = self.get(key, result[key])
            if _valid_preference(key, value):
                result[key] = value
        return result

    def recent_files(self) -> list[dict[str, Any]]:
        try:
            return [dict(row) for row in self._db.execute(
                "SELECT path,name,last_opened FROM recent_files ORDER BY last_opened DESC LIMIT ?",
                (RECENT_FILE_LIMIT,),
            )]
        except sqlite3.Error as exc:
            raise StorageError(f"Cannot read recent files: {exc}") from exc

    def add_recent(self, path: str | Path) -> None:
        file_key, canonical = _file_identity(path)
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO recent_files(file_key,path,name,last_opened) VALUES(?,?,?,?) "
                    "ON CONFLICT(file_key) DO UPDATE SET path=excluded.path,name=excluded.name,last_opened=excluded.last_opened",
                    (file_key, canonical, Path(canonical).name, time.time()),
                )
                self._db.execute(
                    "DELETE FROM recent_files WHERE file_key NOT IN "
                    "(SELECT file_key FROM recent_files ORDER BY last_opened DESC LIMIT ?)",
                    (RECENT_FILE_LIMIT,),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Cannot save recent file: {exc}") from exc

    def remove_recent(self, path: str | Path) -> None:
        file_key, _ = _file_identity(path)
        try:
            with self._db:
                self._db.execute("DELETE FROM recent_files WHERE file_key=?", (file_key,))
        except sqlite3.Error as exc:
            raise StorageError(f"Cannot remove recent-file entry: {exc}") from exc

    def save_resume(
        self,
        path: str | Path,
        fingerprint: Any,
        game_index: int,
        variation_path: Sequence[int],
        orientation: str,
    ) -> None:
        if type(game_index) is not int or game_index < 0:
            raise ValueError("Resume game index must be a non-negative integer.")
        route = list(variation_path)
        if any(type(index) is not int or index < 0 for index in route):
            raise ValueError("Resume variation path must contain non-negative integers.")
        if not _valid_preference("orientation", orientation):
            raise ValueError("Resume orientation must be white or black.")
        file_key, canonical = _file_identity(path)
        encoded_fingerprint, encoded_route = _encode(fingerprint), _encode(route)
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO resume_positions(file_key,path,fingerprint,game_index,variation_path,orientation,updated_at) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(file_key) DO UPDATE SET path=excluded.path,"
                    "fingerprint=excluded.fingerprint,game_index=excluded.game_index,variation_path=excluded.variation_path,"
                    "orientation=excluded.orientation,updated_at=excluded.updated_at",
                    (file_key, canonical, encoded_fingerprint, game_index, encoded_route, orientation, time.time()),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Cannot save playback position: {exc}") from exc

    def load_resume(self, path: str | Path, fingerprint: Any) -> dict[str, Any] | None:
        file_key, _ = _file_identity(path)
        try:
            row = self._db.execute(
                "SELECT path,fingerprint,game_index,variation_path,orientation,updated_at FROM resume_positions "
                "WHERE file_key=? AND fingerprint=?", (file_key, _encode(fingerprint)),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            try:
                route = json.loads(result["variation_path"])
                if not isinstance(route, list) or any(type(index) is not int or index < 0 for index in route):
                    return None
                if type(result["game_index"]) is not int or result["game_index"] < 0:
                    return None
                if not _valid_preference("orientation", result["orientation"]):
                    return None
                result["variation_path"] = route
                result["fingerprint"] = json.loads(result["fingerprint"])
                return result
            except (ValueError, TypeError):
                return None
        except sqlite3.Error as exc:
            raise StorageError(f"Cannot read playback position: {exc}") from exc

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> SettingsStore:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
