"""Disposable, edition-local SQLite indexes; never stores parsed game trees.

Each operation owns its connection on the calling thread. Cache failures are
misses, not PGN failures. Text seek cookies are opaque Python integers, so their
decimal representations are stored as TEXT rather than SQLite's 64-bit INTEGER.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Callable, TYPE_CHECKING
import uuid

import chess

from .models import GameInfo

if TYPE_CHECKING:
    from .pgn_loader import GameCollection


APPLICATION_ID = 0x50474E49  # PGNI; deliberately distinct from preferences.
SCHEMA_VERSION = 1
BATCH_SIZE = 2048
_HEADER_NAME_LIMIT = 1024
_HEADER_NAME_MAX_LENGTH = 128
RUNTIME_ID = (
    f"{sys.implementation.name}-{sys.version_info.major}.{sys.version_info.minor}"
    f";chess-{chess.__version__};text-cookie-v1;newline-universal;errors-replace"
)
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_SCHEMA = {
    "collections": [
        ("id", "INTEGER", 1), ("file_key", "TEXT", 0), ("path", "TEXT", 0),
        ("size", "INTEGER", 0), ("mtime_ns", "INTEGER", 0), ("inode", "TEXT", 0),
        ("encoding", "TEXT", 0), ("fingerprint", "TEXT", 0),
        ("game_count", "INTEGER", 0), ("runtime_id", "TEXT", 0),
        ("index_checksum", "TEXT", 0),
    ],
    "entries": [
        ("collection_id", "INTEGER", 1), ("ordinal", "INTEGER", 2),
        ("cookie", "TEXT", 0), ("headers", "TEXT", 0),
    ],
}


class CacheCancelled(Exception):
    """The worker was cancelled; callers should preserve cancellation semantics."""


@dataclass
class CachedIndex:
    entries: list[GameInfo]
    fingerprint: str
    encoding: str


class _Cancellation:
    def __init__(self, callback: Callable[[], bool] | None) -> None:
        self.callback = callback
        self.cancelled = False

    def poll(self) -> bool:
        self.cancelled = self.cancelled or (self.callback is not None and bool(self.callback()))
        return self.cancelled

    def check(self) -> None:
        if self.poll():
            raise CacheCancelled("PGN loading was cancelled.")


def _identity(path: str | Path) -> tuple[str, str]:
    canonical = str(Path(path).expanduser().resolve())
    return os.path.normcase(canonical), canonical


def _headers_json(headers: dict[str, str]) -> str:
    if not isinstance(headers, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in headers.items()
    ):
        raise ValueError("Invalid PGN headers in saved index.")
    # Keep tag order as well as values: GameInfo.search_text joins that order.
    return json.dumps(headers, ensure_ascii=False, separators=(",", ":"))


def _headers_from_json(encoded: str, names: dict[str, str]) -> dict[str, str]:
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError("Invalid PGN headers in saved index.")
    headers: dict[str, str] = {}
    for key, value in decoded.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("Invalid PGN headers in saved index.")
        # json.loads otherwise allocates fresh copies of every repeated tag
        # name for every game. Sharing common keys saves gigabytes for a
        # multi-million-game index, matching the parser's shared standard tags.
        # Bound this per-load pool so unusual/custom names cannot grow it
        # indefinitely. Values and insertion order remain exactly as recorded.
        shared = names.get(key)
        if shared is not None:
            key = shared
        elif len(names) < _HEADER_NAME_LIMIT and len(key) <= _HEADER_NAME_MAX_LENGTH:
            names[key] = key
        headers[key] = value
    return headers


def _checksum_row(digest, ordinal: int, cookie: str, headers: str) -> None:
    # Length framing makes boundaries unambiguous even for arbitrary PGN tags.
    # This hashes index records only, never opens or hashes the PGN itself.
    for value in (str(ordinal), cookie, headers):
        encoded = value.encode("utf-8")
        digest.update(str(len(encoded)).encode("ascii"))
        digest.update(b":")
        digest.update(encoded)


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and _HEX_DIGEST.fullmatch(value) is not None


def _cookie(value: object) -> int:
    if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None:
        raise ValueError("Invalid text seek cookie in saved index.")
    return int(value)


def _db_stamp(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_ctime_ns
    except OSError:
        return None


def _is_corrupt(error: BaseException) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}
    # Python 3.10 does not attach sqlite_errorcode to exceptions.
    return isinstance(error, sqlite3.DatabaseError) and any(
        message in str(error).lower()
        for message in ("database disk image is malformed", "file is not a database")
    )


class IndexCache:
    """Best-effort index persistence independent of the preferences database.

    The caller supplies this edition's dedicated cache path. Construction does
    not create a directory, connect to SQLite, or retain any worker connection.
    """

    def __init__(self, db_path: str | Path, *, timeout: float = 0.15) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.timeout = timeout

    def _connect(self, *, write: bool) -> sqlite3.Connection:
        # Reject an obviously non-SQLite file before SQLite can inspect/remove
        # sidecars. This is a constant 16-byte header check, never a DB scan.
        try:
            with self.db_path.open("rb") as stream:
                header = stream.read(16)
            if header and header != b"SQLite format 3\x00":
                raise sqlite3.DatabaseError("file is not a database")
        except FileNotFoundError:
            pass
        if write:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            return sqlite3.connect(self.db_path, timeout=self.timeout, isolation_level=None)
        return sqlite3.connect(
            self.db_path.as_uri() + "?mode=ro", uri=True,
            timeout=self.timeout, isolation_level=None,
        )

    @staticmethod
    def _prepare(db: sqlite3.Connection, *, write: bool) -> bool:
        application_id = db.execute("PRAGMA application_id").fetchone()[0]
        version = db.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if application_id != APPLICATION_ID and not (
            application_id == 0 and version == 0 and not tables
        ):
            # In particular, never treat the application's settings DB as cache.
            return False
        valid = application_id == APPLICATION_ID and version == SCHEMA_VERSION
        if valid:
            for table, expected in _SCHEMA.items():
                actual = [(row[1], row[2], row[5]) for row in db.execute(f"PRAGMA table_info({table})")]
                if actual != expected:
                    valid = False
                    break
        if valid or not write:
            return valid
        # Only our explicitly identified, disposable tables may be recreated.
        db.execute("DROP TABLE IF EXISTS entries")
        db.execute("DROP TABLE IF EXISTS collections")
        db.execute(
            "CREATE TABLE collections (id INTEGER PRIMARY KEY, file_key TEXT NOT NULL UNIQUE, "
            "path TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, "
            "inode TEXT NOT NULL, encoding TEXT NOT NULL, fingerprint TEXT NOT NULL, "
            "game_count INTEGER NOT NULL, runtime_id TEXT NOT NULL, index_checksum TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE entries (collection_id INTEGER NOT NULL REFERENCES collections(id) "
            "ON DELETE CASCADE, ordinal INTEGER NOT NULL, cookie TEXT NOT NULL, headers TEXT NOT NULL, "
            "PRIMARY KEY (collection_id, ordinal)) WITHOUT ROWID"
        )
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        return True

    def _quarantine(self, stamp: tuple[int, int, int, int] | None) -> bool:
        # Close our connection first. Refuse to move a DB changed by another
        # worker, or one with a journal/WAL that might belong to a live writer.
        # Windows also refuses the rename while another SQLite handle is open.
        try:
            if stamp is None or _db_stamp(self.db_path) != stamp:
                return False
            if any(Path(str(self.db_path) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")):
                return False
            quarantine = self.db_path.with_name(f"{self.db_path.name}.corrupt-{uuid.uuid4().hex}.bak")
            self.db_path.rename(quarantine)
            return True
        except OSError:
            return False

    @staticmethod
    def _close(db: sqlite3.Connection | None) -> None:
        if db is not None:
            with suppress(sqlite3.Error):
                db.set_progress_handler(None, 0)
                if db.in_transaction:
                    db.rollback()
            with suppress(sqlite3.Error):
                db.close()

    def load(
        self, path: Path, signature: tuple[int, int, int],
        cancel: Callable[[], bool] | None = None,
    ) -> CachedIndex | None:
        cancellation = _Cancellation(cancel)
        cancellation.check()
        db = None
        corrupt = False
        stamp = _db_stamp(self.db_path)
        try:
            key, canonical = _identity(path)
            db = self._connect(write=False)
            db.set_progress_handler(cancellation.poll, 1000)
            db.execute("BEGIN")  # Header and rows belong to the same snapshot.
            if not self._prepare(db, write=False):
                return None
            row = db.execute(
                "SELECT id,path,size,mtime_ns,inode,encoding,fingerprint,game_count,runtime_id,index_checksum "
                "FROM collections WHERE file_key=?", (key,),
            ).fetchone()
            if row is None:
                return None
            collection_id, saved_path, size, mtime, inode, encoding, fingerprint, count, runtime, checksum = row
            if (
                not isinstance(saved_path, str) or os.path.normcase(saved_path) != os.path.normcase(canonical)
                or type(size) is not int or type(mtime) is not int
                or (size, mtime, _cookie(inode)) != signature
                or encoding not in {"utf-8-sig", "cp1252"}
                or not _valid_digest(fingerprint) or not _valid_digest(checksum)
                or type(count) is not int or count < 0 or runtime != RUNTIME_ID
            ):
                return None
            entries: list[GameInfo] = []
            header_names: dict[str, str] = {}
            digest = hashlib.sha256()
            cursor = db.execute(
                "SELECT ordinal,cookie,headers FROM entries WHERE collection_id=? ORDER BY ordinal",
                (collection_id,),
            )
            while True:
                cancellation.check()
                batch = cursor.fetchmany(BATCH_SIZE)
                if not batch:
                    break
                for ordinal, cookie, encoded_headers in batch:
                    if type(ordinal) is not int or ordinal != len(entries) or ordinal >= count:
                        return None
                    if not isinstance(encoded_headers, str):
                        return None
                    offset = _cookie(cookie)
                    headers = _headers_from_json(encoded_headers, header_names)
                    _checksum_row(digest, ordinal, cookie, encoded_headers)
                    entries.append(GameInfo(ordinal, headers, offset))
            cancellation.check()
            if len(entries) != count or digest.hexdigest() != checksum:
                return None
            return CachedIndex(entries, fingerprint, encoding)
        except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError, RecursionError) as error:
            cancellation.check()
            corrupt = _is_corrupt(error)
            return None
        finally:
            self._close(db)
            if corrupt:
                self._quarantine(stamp)

    def save(self, collection: GameCollection, cancel: Callable[[], bool] | None = None) -> bool:
        cancellation = _Cancellation(cancel)
        cancellation.check()
        for attempt in range(2):
            db = None
            corrupt = False
            stamp = _db_stamp(self.db_path)
            try:
                key, canonical = _identity(collection.path)
                size, mtime, inode = collection._signature
                if (
                    type(size) is not int or size < 0 or type(mtime) is not int
                    or type(inode) is not int or inode < 0
                    or collection.encoding not in {"utf-8-sig", "cp1252"}
                    or not _valid_digest(collection.fingerprint)
                ):
                    return False
                db = self._connect(write=True)
                db.set_progress_handler(cancellation.poll, 1000)
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("BEGIN IMMEDIATE")
                if not self._prepare(db, write=True):
                    return False
                # Delete and replacement are one transaction. Cancellation or a
                # write failure leaves the previous complete index available.
                db.execute("DELETE FROM collections WHERE file_key=?", (key,))
                inserted = db.execute(
                    "INSERT INTO collections(file_key,path,size,mtime_ns,inode,encoding,fingerprint,"
                    "game_count,runtime_id,index_checksum) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (key, canonical, size, mtime, str(inode), collection.encoding,
                     collection.fingerprint, len(collection.entries), RUNTIME_ID, ""),
                )
                collection_id = inserted.lastrowid
                digest = hashlib.sha256()
                batch: list[tuple[int, int, str, str]] = []
                for ordinal, entry in enumerate(collection.entries):
                    if type(entry.index) is not int or entry.index != ordinal:
                        return False
                    if type(entry.offset) is not int or entry.offset < 0:
                        return False
                    cookie = str(entry.offset)
                    headers = _headers_json(entry.headers)
                    _checksum_row(digest, ordinal, cookie, headers)
                    batch.append((collection_id, ordinal, cookie, headers))
                    if len(batch) == BATCH_SIZE:
                        cancellation.check()
                        db.executemany("INSERT INTO entries VALUES(?,?,?,?)", batch)
                        batch.clear()
                if batch:
                    cancellation.check()
                    db.executemany("INSERT INTO entries VALUES(?,?,?,?)", batch)
                db.execute(
                    "UPDATE collections SET index_checksum=? WHERE id=?",
                    (digest.hexdigest(), collection_id),
                )
                cancellation.check()
                db.commit()
                return True
            except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError) as error:
                cancellation.check()
                corrupt = _is_corrupt(error)
            finally:
                self._close(db)
            if not corrupt or attempt or not self._quarantine(stamp):
                return False
        return False

    def discard(self, path: Path) -> None:
        """Remove only this file's index; unavailable cache storage is harmless."""
        db = None
        try:
            key, _ = _identity(path)
            if not self.db_path.exists():
                return
            db = self._connect(write=True)
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            if self._prepare(db, write=False):
                db.execute("DELETE FROM collections WHERE file_key=?", (key,))
                db.commit()
        except (OSError, sqlite3.Error, ValueError, TypeError):
            pass
        finally:
            self._close(db)
