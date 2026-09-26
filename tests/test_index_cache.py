"""Persistent indexes use synthetic PGNs and temporary, isolated SQLite files."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from pgn_player import index_cache
from pgn_player.index_cache import APPLICATION_ID, BATCH_SIZE, CacheCancelled, IndexCache, SCHEMA_VERSION
from pgn_player.models import GameInfo
from pgn_player.pgn_loader import GameCollection, index_pgn
from pgn_player.storage import SettingsStore


def collection(tmp_path, name="study.pgn", count=3):
    path = tmp_path / name
    path.write_text("\n\n".join(
        f'[Event "Study {number}"]\n[White "White {number}"]\n[Black "Black"]\n'
        '[Result "*"]\n[Annotator "A synthetic author"]\n[X-Custom "Unicode ♞ and quotes"]\n\n'
        '1. e4 e5 2. Nf3 *'
        for number in range(count)
    ), encoding="utf-8")
    return index_pgn(path)


def synthetic_collection(tmp_path, count):
    path = tmp_path / "synthetic.pgn"
    path.write_text("*", encoding="utf-8")
    stat = path.stat()
    return GameCollection(
        path, [GameInfo(number, {"Event": f"Game {number}", "White": "Synthetic"}, number * 5)
               for number in range(count)],
        hashlib.sha256(b"*").hexdigest(), "utf-8-sig",
        (stat.st_size, stat.st_mtime_ns, stat.st_ino),
    )


def test_missing_cache_is_a_miss_without_creating_storage(tmp_path):
    cache = IndexCache(tmp_path / "new-directory" / "pgn-index-cache.sqlite3")
    assert cache.load(tmp_path / "absent.pgn", (0, 0, 0)) is None
    cache.discard(tmp_path / "absent.pgn")
    assert not cache.db_path.parent.exists()


def test_first_save_reopen_preserves_all_headers_offsets_and_lazy_loading(tmp_path):
    original = collection(tmp_path)
    db_path = tmp_path / "cache.sqlite3"
    assert IndexCache(db_path).save(original)
    restored = IndexCache(db_path).load(original.path, original._signature)
    assert restored is not None
    assert restored.entries == original.entries
    assert restored.fingerprint == original.fingerprint
    assert restored.encoding == original.encoding
    assert restored.entries[1].headers["Annotator"] == "A synthetic author"
    assert "Unicode ♞" in restored.entries[1].headers["X-Custom"]
    lazy = GameCollection(original.path, restored.entries, restored.fingerprint,
                          restored.encoding, original._signature)
    assert not lazy._cache
    assert lazy.entries[2].search_text == original.entries[2].search_text
    for index in (2, 0, 1):
        assert lazy.load_game(index).headers == original.load_game(index).headers
        assert list(lazy.load_game(index).mainline_moves()) == list(original.load_game(index).mainline_moves())
    with sqlite3.connect(db_path) as db:
        saved = db.execute("SELECT path,size,mtime_ns,inode,game_count FROM collections").fetchone()
        assert saved == (str(original.path.resolve()), original._signature[0], original._signature[1],
                         str(original._signature[2]), len(original.entries))


def test_cache_load_does_not_open_pgn_or_compute_its_digest(tmp_path, monkeypatch):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(original)
    native_open = Path.open

    def deny_pgn_open(path, *args, **kwargs):
        assert path != original.path, "A cache hit must not scan PGN bytes."
        return native_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_pgn_open)
    assert cache.load(original.path, original._signature).entries == original.entries


@pytest.mark.parametrize("field", [0, 1, 2])
def test_file_metadata_mismatch_is_a_miss(tmp_path, field):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(original)
    changed = list(original._signature)
    changed[field] += 1
    assert cache.load(original.path, tuple(changed)) is None
    assert cache.load(original.path, original._signature) is not None


def test_different_paths_have_independent_indexes_and_discard_is_scoped(tmp_path):
    first = collection(tmp_path, "first.pgn", 2)
    second = collection(tmp_path, "second.pgn", 4)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(first) and cache.save(second)
    assert cache.load(first.path, first._signature).entries == first.entries
    assert cache.load(second.path, second._signature).entries == second.entries
    cache.discard(first.path)
    assert cache.load(first.path, first._signature) is None
    assert cache.load(second.path, second._signature).entries == second.entries


def test_normalized_paths_identify_same_cached_file(tmp_path):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(original)
    relative_components = original.path.parent / "unused" / ".." / original.path.name
    assert cache.load(relative_components, original._signature).entries == original.entries
    if os.name == "nt":
        assert cache.load(Path(str(original.path).swapcase()), original._signature).entries == original.entries


def test_seek_cookies_and_inode_can_exceed_sqlite_integer_and_file_size(tmp_path):
    original = collection(tmp_path)
    huge = 2 ** 160 + 19
    # Cookies are opaque; neither increasing order nor comparison to byte size
    # is a valid way to validate them.
    original.entries = [GameInfo(i, {"Event": str(i)}, offset) for i, offset in enumerate((huge, 0, 7))]
    original._signature = (*original._signature[:2], 2 ** 90)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(original)
    restored = cache.load(original.path, original._signature)
    assert [entry.offset for entry in restored.entries] == [huge, 0, 7]
    with sqlite3.connect(cache.db_path) as db:
        assert db.execute("SELECT typeof(cookie) FROM entries").fetchall() == [("text",)] * 3
        assert db.execute("SELECT typeof(inode) FROM collections").fetchone() == ("text",)


def test_loaded_games_share_header_names_without_changing_values_or_tag_order(tmp_path):
    original = collection(tmp_path, count=3)
    for entry in original.entries:
        entry.headers["RepeatedTrainingHeader"] = f"Position {entry.index}"
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(original)
    restored = cache.load(original.path, original._signature)
    assert restored.entries == original.entries
    first_names = list(restored.entries[0].headers)
    for entry, original_entry in zip(restored.entries, original.entries):
        assert list(entry.headers) == list(original_entry.headers)
        assert entry.search_text == original_entry.search_text
        assert all(key is shared for key, shared in zip(entry.headers, first_names))
    assert [entry.headers["RepeatedTrainingHeader"] for entry in restored.entries] == [
        "Position 0", "Position 1", "Position 2",
    ]


def test_header_name_pool_is_bounded_and_preserves_unpooled_custom_tags():
    names = {}
    limit = index_cache._HEADER_NAME_LIMIT
    for number in range(limit + 5):
        key = f"CustomTrainingTag_{number}"
        decoded = index_cache._headers_from_json(json.dumps({key: str(number)}), names)
        assert decoded == {key: str(number)}
    assert len(names) == limit
    assert "CustomTrainingTag_0" in names
    assert f"CustomTrainingTag_{limit}" not in names
    long_key = "X" * (index_cache._HEADER_NAME_MAX_LENGTH + 1)
    empty_pool = {}
    assert index_cache._headers_from_json(json.dumps({long_key: "kept"}), empty_pool) == {long_key: "kept"}
    assert empty_pool == {}
    # A full pool still reuses already-known names instead of allocating a new
    # copy for each later game.
    decoded = index_cache._headers_from_json('{"CustomTrainingTag_0":"later"}', names)
    assert next(iter(decoded)) is names["CustomTrainingTag_0"]


@pytest.mark.parametrize("encoding", ["utf-8-sig", "cp1252"])
def test_both_supported_encodings_and_empty_indexes_round_trip(tmp_path, encoding):
    original = collection(tmp_path, count=0)
    original.encoding = encoding
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(original)
    restored = cache.load(original.path, original._signature)
    assert restored.entries == []
    assert restored.encoding == encoding


@pytest.mark.parametrize("sql,parameters", [
    ("UPDATE entries SET cookie=? WHERE ordinal=1", ("123456789",)),
    ("UPDATE entries SET headers=? WHERE ordinal=1", ('{"Event":"changed"}',)),
    ("DELETE FROM entries WHERE ordinal=1", ()),
    ("UPDATE entries SET ordinal=30 WHERE ordinal=1", ()),
    ("UPDATE collections SET game_count=game_count+1", ()),
    ("UPDATE collections SET runtime_id='another-python-runtime'", ()),
    ("UPDATE collections SET fingerprint='broken'", ()),
    ("UPDATE collections SET index_checksum='broken'", ()),
    ("UPDATE collections SET encoding='utf-16'", ()),
    ("UPDATE collections SET path='not-a-canonical-path'", ()),
    ("UPDATE entries SET headers=? WHERE ordinal=1", (sqlite3.Binary(b'{}'),)),
    ("UPDATE entries SET headers=? WHERE ordinal=1", ('{"Event":123}',)),
    ("UPDATE entries SET headers=? WHERE ordinal=1", ('["not", "headers"]',)),
    ("UPDATE entries SET headers=? WHERE ordinal=1", ('invalid-json',)),
    ("UPDATE entries SET headers=? WHERE ordinal=1", ('[' * 1500 + ']' * 1500,)),
])
def test_corrupt_cache_records_are_misses_and_can_be_replaced(tmp_path, sql, parameters):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(original)
    with sqlite3.connect(cache.db_path) as db:
        db.execute(sql, parameters)
    assert cache.load(original.path, original._signature) is None
    assert cache.save(original)
    assert cache.load(original.path, original._signature).entries == original.entries


@pytest.mark.parametrize("cookie", ["-1", "01", "+1", "1.0", "1e3", " 1", "1\n", "١", b"12"])
def test_seek_cookie_requires_canonical_nonnegative_decimal(tmp_path, cookie):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(original)
    with sqlite3.connect(cache.db_path) as db:
        db.execute("UPDATE entries SET cookie=? WHERE ordinal=1", (cookie,))
    assert cache.load(original.path, original._signature) is None


@pytest.mark.parametrize("damage", ["old-version", "wrong-columns", "missing-table"])
def test_obsolete_or_invalid_owned_schema_is_recreated(tmp_path, damage):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(original)
    with sqlite3.connect(cache.db_path) as db:
        if damage == "old-version":
            db.execute("PRAGMA user_version=0")
        else:
            db.execute("DROP TABLE entries")
            if damage == "wrong-columns":
                db.execute("CREATE TABLE entries(wrong TEXT)")
    assert cache.load(original.path, original._signature) is None
    assert cache.save(original)
    assert cache.load(original.path, original._signature).entries == original.entries
    with sqlite3.connect(cache.db_path) as db:
        assert db.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


@pytest.mark.parametrize("application_id", [0, 123456])
def test_foreign_database_is_preserved_and_refuses_writes(tmp_path, application_id):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "foreign.sqlite3")
    with sqlite3.connect(cache.db_path) as db:
        db.execute(f"PRAGMA application_id={application_id}")
        db.execute("CREATE TABLE personal_notes (note TEXT)")
        db.execute("INSERT INTO personal_notes VALUES('preserve locally')")
    before = cache.db_path.read_bytes()
    assert cache.load(original.path, original._signature) is None
    assert not cache.save(original)
    cache.discard(original.path)
    assert cache.db_path.read_bytes() == before
    assert not list(tmp_path.glob("*.bak"))


def test_settings_database_is_never_treated_as_disposable_cache(tmp_path):
    original = collection(tmp_path)
    settings_path = tmp_path / "settings.sqlite3"
    with SettingsStore(settings_path) as settings:
        settings.set("theme", "dark")
    cache = IndexCache(settings_path)
    assert cache.load(original.path, original._signature) is None
    assert not cache.save(original)
    cache.discard(original.path)
    with SettingsStore(settings_path) as settings:
        assert settings.get("theme") == "dark"


@pytest.mark.parametrize("first_operation", ["load", "save"])
def test_whole_corrupt_cache_is_preserved_locally_and_recovers(tmp_path, first_operation):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "pgn-index-cache.sqlite3")
    damaged = b"An unusable cache; retain these bytes in a local backup."
    cache.db_path.write_bytes(damaged)
    if first_operation == "load":
        assert cache.load(original.path, original._signature) is None
        assert cache.save(original)
    else:
        assert cache.save(original)
    assert cache.load(original.path, original._signature).entries == original.entries
    backups = list(tmp_path.glob("pgn-index-cache.sqlite3.corrupt-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == damaged


def test_corrupt_cache_with_possible_active_sidecar_is_not_moved(tmp_path):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    damaged = b"broken database"
    cache.db_path.write_bytes(damaged)
    sidecar = Path(str(cache.db_path) + "-wal")
    sidecar.write_bytes(b"preserve possible writer state")
    assert cache.load(original.path, original._signature) is None
    assert not cache.save(original)
    assert cache.db_path.read_bytes() == damaged
    assert sidecar.read_bytes() == b"preserve possible writer state"


def test_busy_cache_does_not_block_loading_or_overwrite_existing_index(tmp_path):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3", timeout=0.01)
    assert cache.save(original)
    with sqlite3.connect(cache.db_path) as locker:
        locker.execute("BEGIN EXCLUSIVE")
        assert cache.load(original.path, original._signature) is None
        assert not cache.save(original)
        cache.discard(original.path)
    assert cache.load(original.path, original._signature).entries == original.entries
    assert not list(tmp_path.glob("*.bak"))


def test_unwritable_cache_is_optional(tmp_path, monkeypatch):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3")

    def denied(**_kwargs):
        raise PermissionError("Cache folder is read-only")

    monkeypatch.setattr(cache, "_connect", denied)
    assert cache.load(original.path, original._signature) is None
    assert not cache.save(original)
    cache.discard(original.path)
    # The source remains independently readable.
    assert list(original.load_game(0).mainline_moves())


def track_connections(monkeypatch):
    native_connect = sqlite3.connect
    connections = []

    class TrackedCursor(sqlite3.Cursor):
        def fetchmany(self, size=None):
            self.connection.fetch_sizes.append(size)
            return super().fetchmany(size)

        def fetchall(self):
            raise AssertionError("Index rows must be read with bounded fetchmany calls")

    class TrackedConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.batch_sizes = []
            self.fetch_sizes = []
            self.statements = []
            self.commits = 0
            self.closed = False
            self.after_batch = None

        def execute(self, sql, parameters=()):
            self.statements.append(sql)
            return self.cursor(factory=TrackedCursor).execute(sql, parameters)

        def executemany(self, sql, parameters):
            self.batch_sizes.append(len(parameters))
            result = super().executemany(sql, parameters)
            if self.after_batch is not None:
                self.after_batch()
            return result

        def commit(self):
            self.commits += 1
            return super().commit()

        def close(self):
            self.closed = True
            return super().close()

    def connect(*args, **kwargs):
        kwargs["factory"] = TrackedConnection
        db = native_connect(*args, **kwargs)
        connections.append(db)
        return db

    monkeypatch.setattr(index_cache.sqlite3, "connect", connect)
    return connections


def test_large_index_uses_bounded_batches_and_one_atomic_save_transaction(tmp_path, monkeypatch):
    original = synthetic_collection(tmp_path, 50000)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    connections = track_connections(monkeypatch)
    assert cache.save(original)
    restored = cache.load(original.path, original._signature)
    assert restored.entries == original.entries
    writer, reader = connections
    assert writer.commits == 1
    assert writer.statements.count("BEGIN IMMEDIATE") == 1
    assert sum(writer.batch_sizes) == len(original.entries)
    assert len(writer.batch_sizes) == (len(original.entries) + BATCH_SIZE - 1) // BATCH_SIZE
    assert max(writer.batch_sizes) <= BATCH_SIZE
    assert set(reader.fetch_sizes) == {BATCH_SIZE}
    assert all("quick_check" not in statement.lower() for db in connections for statement in db.statements)
    assert all(db.closed for db in connections)


def test_cancellation_rolls_back_partial_replacement(tmp_path, monkeypatch):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(original)
    replacement = GameCollection(original.path,
                                 [GameInfo(i, {"Event": "replacement"}, i) for i in range(BATCH_SIZE * 2 + 1)],
                                 original.fingerprint, original.encoding, original._signature)
    connections = track_connections(monkeypatch)
    cancelled = False
    native_connect = cache._connect

    def cancel_after_batch():
        nonlocal cancelled
        cancelled = True

    def connect(**kwargs):
        db = native_connect(**kwargs)
        db.after_batch = cancel_after_batch
        return db

    monkeypatch.setattr(cache, "_connect", connect)
    with pytest.raises(CacheCancelled):
        cache.save(replacement, cancel=lambda: cancelled)
    assert connections[0].batch_sizes == [BATCH_SIZE]
    assert connections[0].commits == 0 and connections[0].closed
    assert cache.load(original.path, original._signature).entries == original.entries


@pytest.mark.parametrize("operation", ["load", "save"])
def test_already_cancelled_operation_does_not_touch_sqlite(tmp_path, monkeypatch, operation):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3")

    def forbidden(**_kwargs):
        raise AssertionError("Cancelled operations must not open SQLite")

    monkeypatch.setattr(cache, "_connect", forbidden)
    with pytest.raises(CacheCancelled):
        if operation == "load":
            cache.load(original.path, original._signature, cancel=lambda: True)
        else:
            cache.save(original, cancel=lambda: True)


def test_cancellation_during_cached_read_propagates_and_preserves_cache(tmp_path):
    original = synthetic_collection(tmp_path, BATCH_SIZE * 3)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(original)
    polls = 0

    def cancel():
        nonlocal polls
        polls += 1
        return polls > 4

    with pytest.raises(CacheCancelled):
        cache.load(original.path, original._signature, cancel=cancel)
    assert cache.load(original.path, original._signature).entries == original.entries


def test_cache_object_can_cross_threads_without_sharing_sqlite_connections(tmp_path):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    with ThreadPoolExecutor(max_workers=1) as worker:
        assert worker.submit(cache.save, original).result()
    with ThreadPoolExecutor(max_workers=1) as another_worker:
        restored = another_worker.submit(cache.load, original.path, original._signature).result()
    assert restored.entries == original.entries


def test_invalid_save_never_replaces_previous_index(tmp_path):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(original)
    previous_entries = original.entries
    original.entries = [GameInfo(0, {"Event": "valid"}, 0), GameInfo(5, {}, 1)]
    assert not cache.save(original)
    assert cache.load(original.path, original._signature).entries == previous_entries


def test_sqlite_write_failure_rolls_back_previous_index(tmp_path, monkeypatch):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(original)
    replacement = GameCollection(original.path,
                                 [GameInfo(i, {"Event": "replacement"}, i) for i in range(BATCH_SIZE * 2)],
                                 original.fingerprint, original.encoding, original._signature)
    connections = track_connections(monkeypatch)
    native_connect = cache._connect

    def fail_after_batch():
        raise sqlite3.OperationalError("database or disk is full")

    def connect(**kwargs):
        db = native_connect(**kwargs)
        db.after_batch = fail_after_batch
        return db

    monkeypatch.setattr(cache, "_connect", connect)
    assert not cache.save(replacement)
    assert connections[0].commits == 0 and connections[0].closed
    assert cache.load(original.path, original._signature).entries == original.entries


def test_reader_sees_complete_old_index_during_replacement(tmp_path, monkeypatch):
    original = collection(tmp_path)
    cache = IndexCache(tmp_path / "cache.sqlite3")
    assert cache.save(original)
    replacement = GameCollection(original.path,
                                 [GameInfo(i, {"Event": "replacement"}, i) for i in range(BATCH_SIZE + 1)],
                                 original.fingerprint, original.encoding, original._signature)
    track_connections(monkeypatch)
    native_connect = cache._connect
    observations = []

    def read_during_write():
        observations.append(IndexCache(cache.db_path).load(original.path, original._signature).entries)

    def connect(**kwargs):
        db = native_connect(**kwargs)
        db.after_batch = read_during_write
        return db

    monkeypatch.setattr(cache, "_connect", connect)
    assert cache.save(replacement)
    assert observations == [original.entries, original.entries]
    assert cache.load(original.path, original._signature).entries == replacement.entries
