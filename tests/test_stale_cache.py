"""Stale-cache regressions use only disposable, synthetic PGN collections."""

import os
import io
from pathlib import Path
import sqlite3

import pytest

from pgn_player.pgn_loader import CachedIndexError, FileChangedError, IndexingCancelled, index_pgn, open_pgn
from pgn_player.index_cache import IndexCache
from pgn_player.playback import PlaybackController
from pgn_player.storage import SettingsStore


RECORDS = (
    '[Event "First"]\n[White "White"]\n[Black "Black"]\n\n1. e4 e5 2. Nf3 Nc6 *\n\n',
    '[Event "Other"]\n[White "White"]\n[Black "Black"]\n\n1. d4 d5 2. c4 e6 *\n\n',
    '[Event "Third"]\n[White "White"]\n[Black "Black"]\n\n1. c4 e5 2. Nc3 Nf6 *\n',
)


def _write_preserving_metadata(path, replacement):
    before = path.stat()
    raw = replacement.encode("utf-8") if isinstance(replacement, str) else replacement
    assert len(raw) == before.st_size
    # In-place writes retain file identity, unlike replacing the directory entry.
    with path.open("r+b") as stream:
        stream.write(raw)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns, after.st_ino) == (
        before.st_size, before.st_mtime_ns, before.st_ino,
    )
    return raw


def _fixture(tmp_path):
    source, cache = tmp_path / "study.pgn", tmp_path / "index.sqlite3"
    source.write_bytes("".join(RECORDS).encode("utf-8"))
    return source, cache, open_pgn(source, cache_path=cache)


def _no_index(*args, **kwargs):
    raise AssertionError("An unchanged, validated index must not be rebuilt")


def _moves(game):
    return [move.uci() for move in game.mainline_moves()]


@pytest.mark.parametrize("reuse_current", [False, True], ids=["saved-index", "current-index"])
@pytest.mark.parametrize("change", ["moves", "header", "reorder", "game-count"])
def test_same_size_restored_timestamp_rebuilds_changed_content(tmp_path, reuse_current, change):
    source, cache, original = _fixture(tmp_path)
    text = "".join(RECORDS)
    if change == "moves":
        changed = text.replace("1. e4 e5", "1. d4 d5", 1)
    elif change == "header":
        changed = text.replace('[Event "First"]', '[Event "Fresh"]', 1)
    elif change == "reorder":
        changed = "".join((RECORDS[2], RECORDS[0], RECORDS[1]))
    else:
        changed = RECORDS[0].rstrip() + "\n"
        changed += " " * (len(text) - len(changed))
    changed_bytes = _write_preserving_metadata(source, changed)
    expected = index_pgn(source)
    scans = []

    def rebuild(path, **kwargs):
        scans.append(path)
        return index_pgn(path, **kwargs)

    actual = open_pgn(source, cache_path=cache, indexer=rebuild,
                      current_collection=original if reuse_current else None)
    assert scans == [source.resolve()]
    assert not actual.from_cache
    assert actual.fingerprint == expected.fingerprint != original.fingerprint
    assert actual.entries == expected.entries
    assert [str(actual.load_game(i)) for i in range(len(actual))] == [
        str(expected.load_game(i)) for i in range(len(expected))
    ]
    saved = open_pgn(source, cache_path=cache, indexer=_no_index)
    assert saved.from_cache and saved.entries == expected.entries
    assert source.read_bytes() == changed_bytes


@pytest.mark.parametrize("change", ["size", "mtime", "replacement"])
def test_ordinary_changes_still_rebuild_once(tmp_path, change):
    source, cache, first = _fixture(tmp_path)
    before = source.stat()
    if change == "size":
        with source.open("ab") as stream:
            stream.write(b'\n[Event "Added"]\n\n1. Nf3 d5 *\n')
    elif change == "mtime":
        source.write_bytes(source.read_bytes().replace(b"1. e4 e5", b"1. d4 d5", 1))
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 10_000_000))
    else:
        replacement = tmp_path / "replacement.pgn"
        replacement.write_bytes(source.read_bytes().replace(b"1. e4 e5", b"1. d4 d5", 1))
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        replacement.replace(source)
    changed = source.read_bytes()
    second = open_pgn(source, cache_path=cache, current_collection=first)
    assert not second.from_cache and second.fingerprint != first.fingerprint
    expected = index_pgn(source)
    assert second.entries == expected.entries
    assert open_pgn(source, cache_path=cache, indexer=_no_index).from_cache
    assert source.read_bytes() == changed


@pytest.mark.parametrize("already_loaded", [False, True])
def test_source_changed_while_open_cannot_return_a_known_stale_game(tmp_path, already_loaded):
    source, cache, _ = _fixture(tmp_path)
    opened = open_pgn(source, cache_path=cache, indexer=_no_index)
    if already_loaded:
        assert opened.load_game(0).next().san() == "e4"
    changed = _write_preserving_metadata(source, "".join(RECORDS).replace("1. e4 e5", "1. d4 d5", 1))
    with pytest.raises((FileChangedError, CachedIndexError)):
        opened.load_game(0)
    assert opened.invalid
    assert source.read_bytes() == changed


def test_changed_collection_does_not_restore_old_game_number_or_move(tmp_path):
    source, cache, first = _fixture(tmp_path)
    with SettingsStore(tmp_path / "settings.sqlite3") as settings:
        settings.set("theme", "dark")
        settings.save_resume(source, first.fingerprint, 2, (0, 0), "black")
        changed = _write_preserving_metadata(source, "".join((RECORDS[2], RECORDS[0], RECORDS[1])))
        reopened = open_pgn(source, cache_path=cache)
        assert reopened.fingerprint != first.fingerprint
        assert settings.load_resume(source, reopened.fingerprint) is None
        # Preserve old local records and preferences rather than deleting them.
        old = settings.load_resume(source, first.fingerprint)
        assert old is not None and old["game_index"] == 2
        assert settings.get("theme") == "dark"
        player = PlaybackController()
        player.set_collection(reopened)
        assert player.game.headers["Event"] == "Third" and player.path == ()
        assert source.read_bytes() == changed


def test_same_metadata_change_during_cache_read_is_not_returned_as_valid(tmp_path, monkeypatch):
    source, cache, first = _fixture(tmp_path)
    load = IndexCache.load
    changed = "".join(RECORDS).replace("1. e4 e5", "1. d4 d5", 1)

    def change_after_read(self, path, signature, cancel=None, **kwargs):
        saved = load(self, path, signature, cancel=cancel, **kwargs)
        _write_preserving_metadata(source, changed)
        return saved

    with monkeypatch.context() as patch:
        patch.setattr(IndexCache, "load", change_after_read)
        reopened = open_pgn(source, cache_path=cache)
    assert not reopened.from_cache
    assert reopened.fingerprint != first.fingerprint
    assert reopened.load_game(0).next().san() == "d4"
    assert open_pgn(source, cache_path=cache, indexer=_no_index).from_cache
    assert source.read_bytes() == changed.encode("utf-8")


@pytest.mark.parametrize("damage", ["offset", "database"])
def test_corrupt_cached_state_rebuilds_without_touching_source_or_settings(tmp_path, damage):
    source, cache, first = _fixture(tmp_path)
    raw = source.read_bytes()
    settings_path = tmp_path / "settings.sqlite3"
    with SettingsStore(settings_path) as settings:
        settings.set("theme", "dark")
        settings.save_resume(source, first.fingerprint, 1, (0,), "black")
    settings_before = settings_path.read_bytes()
    if damage == "offset":
        with sqlite3.connect(cache) as db:
            db.execute("UPDATE entries SET cookie='999999999' WHERE ordinal=1")
    else:
        cache.write_bytes(b"Corrupt disposable index fixture")
    rebuilt = open_pgn(source, cache_path=cache)
    assert not rebuilt.from_cache and rebuilt.entries == first.entries
    assert _moves(rebuilt.load_game(1)) == ["d2d4", "d7d5", "c2c4", "e7e6"]
    assert open_pgn(source, cache_path=cache, indexer=_no_index).from_cache
    assert source.read_bytes() == raw
    assert settings_path.read_bytes() == settings_before


def test_unchanged_collection_keeps_resume_and_invalid_path_does_not_partially_apply(tmp_path):
    source, cache, first = _fixture(tmp_path)
    with SettingsStore(tmp_path / "settings.sqlite3") as settings:
        settings.save_resume(source, first.fingerprint, 1, (0, 0), "black")
        reopened = open_pgn(source, cache_path=cache, indexer=_no_index)
        assert reopened.from_cache and reopened.fingerprint == first.fingerprint
        saved = settings.load_resume(source, reopened.fingerprint)
        player = PlaybackController()
        player.set_collection(reopened)
        assert player.select_game(saved["game_index"])
        assert player.go_to(saved["variation_path"])
        assert player.game.headers["Event"] == "Other" and player.node.san() == "d5"
        board, path = player.board.fen(), player.path
        assert not player.go_to((0, 1, 99))
        assert player.path == path and player.board.fen() == board


@pytest.mark.parametrize("size", [0, 1, 4096, 32768, 32769, 1024 * 1024, 5 * 1024 ** 3])
def test_sample_ranges_and_reads_stay_bounded_even_for_five_gigabytes(size, monkeypatch):
    from pgn_player.source_validation import MAX_SAMPLE_BYTES, SAMPLE_BYTES, sample_ranges, source_sample

    ranges = sample_ranges(size)
    assert sum(length for _, length in ranges) <= min(size, MAX_SAMPLE_BYTES)
    assert all(0 <= offset <= size and 0 <= length <= MAX_SAMPLE_BYTES and offset + length <= size
               for offset, length in ranges)
    if size <= MAX_SAMPLE_BYTES:
        assert sum(length for _, length in ranges) == size
    else:
        assert ranges[0][0] == 0
        assert ranges[-1][0] + ranges[-1][1] == size

    # This virtual file has the requested logical length, without allocating a
    # multi-gigabyte fixture on the developer's disk or in memory.
    class VirtualFile(io.RawIOBase):
        def __init__(self):
            self.position = self.bytes_read = 0

        def read(self, length=-1):
            assert 0 <= length <= MAX_SAMPLE_BYTES, "Source validation attempted an unbounded read"
            actual = min(length, size - self.position)
            self.position += actual
            self.bytes_read += actual
            assert self.bytes_read <= MAX_SAMPLE_BYTES
            return b"x" * actual

        def seek(self, offset, whence=0):
            assert whence == 0 and 0 <= offset <= size
            self.position = offset
            return offset

        def tell(self):
            return self.position

    handle = VirtualFile()
    path = Path("virtual-five-gigabyte.pgn").resolve()

    def virtual_open(candidate, mode="r", *args, **kwargs):
        assert candidate == path and mode == "rb"
        return handle

    monkeypatch.setattr(Path, "open", virtual_open)
    digest = source_sample(path, size)
    assert isinstance(digest, str) and len(digest) == 64
    assert handle.bytes_read <= min(size, MAX_SAMPLE_BYTES)
    assert SAMPLE_BYTES <= MAX_SAMPLE_BYTES


class _CountingSource:
    def __init__(self, stream, totals):
        self.stream, self.totals = stream, totals

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def read(self, size=-1):
        assert size >= 0, "Cache reuse attempted to read the complete source"
        value = self.stream.read(size)
        self.totals.append(len(value))
        return value

    def readline(self, *args):
        raise AssertionError("Cache reuse must not parse every game's movetext")


@pytest.mark.parametrize("reuse_current", [False, True])
def test_large_unchanged_index_reuses_without_linear_source_validation(tmp_path, monkeypatch, reuse_current):
    from pgn_player.source_validation import MAX_SAMPLE_BYTES

    source, cache = tmp_path / "large-study.pgn", tmp_path / "index.sqlite3"
    record = '[Event "Study"]\n\n1. e4 {' + "A" * 6000 + '} e5 *\n\n'
    source.write_bytes((record * 200).encode("ascii"))
    first = open_pgn(source, cache_path=cache)
    assert len(first) == 200 and source.stat().st_size > 1024 * 1024
    opened = Path.open
    reads = []

    def count_source(path, mode="r", *args, **kwargs):
        stream = opened(path, mode, *args, **kwargs)
        return _CountingSource(stream, reads) if path.resolve() == source.resolve() else stream

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", count_source)
        second = open_pgn(source, cache_path=cache, indexer=_no_index,
                          current_collection=first if reuse_current else None)
    assert second.from_cache and second.fingerprint == first.fingerprint
    assert second.entries == first.entries and second._cache == {}
    assert 0 < sum(reads) <= 4 * MAX_SAMPLE_BYTES
    if reuse_current:
        assert second.entries is first.entries


def test_resume_digest_is_optional_and_does_not_reset_legacy_records(tmp_path):
    source = tmp_path / "study.pgn"
    source.write_text("1. e4 *\n", encoding="utf-8")
    settings_path = tmp_path / "settings.sqlite3"
    with SettingsStore(settings_path) as settings:
        settings.set("theme", "dark")
        settings.save_resume(source, "fingerprint", 2, (0, 1), "black")
        legacy = settings.load_resume(source, "fingerprint")
        assert legacy is not None and legacy.get("game_digest") is None
        settings.save_resume(source, "fingerprint", 2, (0, 1), "black", game_digest="a" * 64)
    with SettingsStore(settings_path) as reopened:
        saved = reopened.load_resume(source, "fingerprint")
        assert saved["game_digest"] == "a" * 64
        assert saved["game_index"] == 2 and saved["variation_path"] == [0, 1]
        assert reopened.get("theme") == "dark"


def test_bounded_sample_can_be_cancelled_before_the_next_read(tmp_path, monkeypatch):
    from pgn_player.source_validation import SAMPLE_BYTES, source_sample

    source = tmp_path / "sample.pgn"
    source.write_bytes(b"x" * (1024 * 1024))
    size = source.stat().st_size
    original_open = Path.open
    reads = []
    cancellation_checks = []

    def count_source(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return _CountingSource(stream, reads) if path == source else stream

    def cancel():
        cancellation_checks.append(True)
        if len(cancellation_checks) == 2:
            raise IndexingCancelled("Cancelled during source validation")

    monkeypatch.setattr(Path, "open", count_source)
    with pytest.raises(IndexingCancelled, match="source validation"):
        source_sample(source, size, cancel=cancel)
    assert reads == [SAMPLE_BYTES]
    assert len(cancellation_checks) == 2


def test_move_navigation_uses_loaded_snapshot_without_source_io(tmp_path, monkeypatch):
    source, _, collection = _fixture(tmp_path)
    player = PlaybackController()
    player.set_collection(collection)
    loaded_game = player.game
    _write_preserving_metadata(source, "".join(RECORDS).replace("1. e4 e5", "1. d4 d5", 1))
    original_open, original_stat = Path.open, Path.stat

    def no_source_open(path, *args, **kwargs):
        assert path != source, "Stepping within a loaded game must not reread the PGN"
        return original_open(path, *args, **kwargs)

    def no_source_stat(path, *args, **kwargs):
        assert path != source, "Stepping within a loaded game must not stat the PGN"
        return original_stat(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", no_source_open)
        patch.setattr(Path, "stat", no_source_stat)
        for expected in ("e4", "e5", "Nf3", "Nc6"):
            assert player.next() and player.node.san() == expected
        assert not player.next()
        assert player.previous() and player.node.san() == "Nf3"
        assert player.first() and player.path == ()
        assert not player.previous()
        assert player.last() and player.node.san() == "Nc6"
        assert player.game is loaded_game

    # The next game selection validates the collection before replacing state.
    with pytest.raises(FileChangedError):
        player.next_game()
    assert collection.invalid
    assert player.game is loaded_game and player.game_index == 0
    assert player.node.san() == "Nc6"


@pytest.mark.parametrize("boundary", ["next", "final"])
def test_headerless_game_rejects_checksum_valid_wrong_offset(tmp_path, boundary):
    from pgn_player.models import GameInfo
    from pgn_player.source_validation import source_sample

    source, cache = tmp_path / "headerless.pgn", tmp_path / "index.sqlite3"
    record = '1. e4 e5 2. Nf3 Nc6 {' + "A" * 6000 + '} *\n\n'
    raw = (record * 100).encode("ascii")
    source.write_bytes(raw)
    original = open_pgn(source, cache_path=cache)
    assert len(original) == 100
    selected = 2 if boundary == "next" else len(original) - 1
    assert original.entries[selected].headers == {}
    # Re-save deliberately inconsistent index data through the ordinary cache
    # writer, so its checksum is valid and cannot be the reason for rejection.
    if boundary == "next":
        next_entry = original.entries[selected + 1]
        original.entries[selected + 1] = GameInfo(
            next_entry.index, next_entry.headers, next_entry.offset + 1,
        )
    else:
        final_entry = original.entries[selected]
        earlier_entry = original.entries[selected - 1]
        assert final_entry.headers == earlier_entry.headers
        original.entries[selected] = GameInfo(
            final_entry.index, final_entry.headers, earlier_entry.offset,
        )
    assert IndexCache(cache).save(original)
    assert source_sample(source, len(raw)) == original.source_sample
    cached = open_pgn(source, cache_path=cache, indexer=_no_index)
    assert cached.from_cache
    assert cached.entries[selected].headers == {}
    with pytest.raises(CachedIndexError, match="boundary"):
        cached.load_game(selected)
    assert cached.invalid
    assert source.read_bytes() == raw
