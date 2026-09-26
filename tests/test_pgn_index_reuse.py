"""Persistent-index integration without reading PGN contents on cache hits."""
from pathlib import Path
import os
import weakref

import pytest

from pgn_player.models import GameInfo
from pgn_player.paths import resolve_paths
from pgn_player.pgn_loader import CachedIndexError, IndexingCancelled, open_pgn


FIXTURES = Path(__file__).parent / "fixtures"


def forbidden_index(*args, **kwargs):
    raise AssertionError("An unchanged PGN must not be scanned or hashed again")


def test_first_open_saves_and_reopen_never_reads_the_pgn(tmp_path, monkeypatch):
    source = FIXTURES / "multiple_games.pgn"
    cache = tmp_path / "pgn-index-cache.sqlite3"
    phases, progress = [], []
    first = open_pgn(source, cache_path=cache, status=phases.append, progress=progress.append)
    assert not first.from_cache
    assert phases == ["cache", "indexing", "saving"]
    assert progress[0] == 0 and progress[-1] == 100
    assert cache.exists()
    original_open = Path.open

    def no_source_read(path, *args, **kwargs):
        assert path.resolve() != source.resolve(), "Cache lookup opened the PGN"
        return original_open(path, *args, **kwargs)

    phases.clear()
    progress.clear()
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", no_source_read)
        second = open_pgn(source, cache_path=cache, indexer=forbidden_index,
            status=phases.append, progress=progress.append)
    assert second.from_cache
    assert second.entries == first.entries
    assert second.fingerprint == first.fingerprint
    assert second._cache == {}
    assert phases == ["cache"] and progress == []
    assert second.load_game(2).next().san() == "Nf3"
    assert second.load_game(0).next().san() == "e4"


@pytest.mark.parametrize("filename", ["annotations_variations.pgn", "special_positions.pgn", "malformed.pgn"])
def test_cached_offsets_preserve_actual_games_and_headers(tmp_path, filename):
    source, cache = FIXTURES / filename, tmp_path / "cache.sqlite3"
    first = open_pgn(source, cache_path=cache)
    second = open_pgn(source, cache_path=cache, indexer=forbidden_index)
    for index in range(len(first)):
        assert second.entries[index].headers == first.entries[index].headers
        assert str(second.load_game(index)) == str(first.load_game(index))
    assert len(second._cache) <= 8


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_cached_text_cookies_round_trip_encodings_and_newlines(tmp_path, encoding, newline):
    source, cache = tmp_path / "encoding.pgn", tmp_path / "cache.sqlite3"
    text = '[White "René"]\n[CustomTag "Défense"]\n\n1. e4 *\n\n[White "Zoë"]\n\n1. d4 *\n'
    source.write_bytes(text.replace("\n", newline).encode(encoding))
    first = open_pgn(source, cache_path=cache)
    second = open_pgn(source, cache_path=cache, indexer=forbidden_index)
    assert first.entries == second.entries
    assert second.entries[0].headers["CustomTag"] == "Défense"
    assert second.load_game(1).headers["White"] == "Zoë"
    assert second.load_game(0).next().san() == "e4"
    assert bool(second.warnings) == (encoding == "cp1252")


@pytest.mark.parametrize("change", ["same_size_edit", "append", "replace"])
def test_changed_metadata_reindexes_and_replaces_cached_index(tmp_path, change):
    source, cache = tmp_path / "games.pgn", tmp_path / "cache.sqlite3"
    source.write_text('[White "First"]\n\n1. e4 *\n', encoding="utf-8")
    first = open_pgn(source, cache_path=cache)
    before = source.stat()
    if change == "append":
        with source.open("a", encoding="utf-8") as stream:
            stream.write('\n[White "Second"]\n\n1. d4 *\n')
    elif change == "replace":
        replacement = tmp_path / "replacement.pgn"
        replacement.write_text('[White "Replacement"]\n\n1. Nf3 *\n', encoding="utf-8")
        replacement.replace(source)
    else:
        source.write_text('[White "Other"]\n\n1. d4 *\n', encoding="utf-8")
        assert source.stat().st_size == before.st_size
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 10_000_000))
    phases = []
    rebuilt = open_pgn(source, cache_path=cache, status=phases.append)
    assert not rebuilt.from_cache and "indexing" in phases
    assert rebuilt.fingerprint != first.fingerprint
    cached = open_pgn(source, cache_path=cache, indexer=forbidden_index)
    assert cached.entries == rebuilt.entries
    assert len(cached) == (2 if change == "append" else 1)
    assert cached.load_game(len(cached) - 1).next().san() == ("Nf3" if change == "replace" else "d4")


def test_corrupt_database_falls_back_then_future_opens_use_rebuilt_cache(tmp_path):
    source, cache = FIXTURES / "multiple_games.pgn", tmp_path / "cache.sqlite3"
    open_pgn(source, cache_path=cache)
    cache.write_bytes(b"damaged disposable PGN index")
    rebuilt = open_pgn(source, cache_path=cache)
    assert len(rebuilt) == 3 and not rebuilt.from_cache
    assert open_pgn(source, cache_path=cache, indexer=forbidden_index).from_cache


def test_cache_failure_still_opens_pgn_and_cancellation_never_indexes(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("Preserve this unrelated file", encoding="utf-8")
    collection = open_pgn(FIXTURES / "mainline.pgn", cache_path=blocked / "cache.sqlite3")
    assert collection.load_game(0).next().san() == "e4"
    assert collection.warnings
    assert blocked.read_text() == "Preserve this unrelated file"
    with pytest.raises(IndexingCancelled):
        open_pgn(FIXTURES / "mainline.pgn", cache_path=tmp_path / "cache.sqlite3",
            cancel=lambda: True, indexer=forbidden_index)


def test_portable_installed_and_development_indexes_are_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    locations = [resolve_paths(edition, app_dir=tmp_path / "application").data_dir / "pgn-index-cache.sqlite3"
        for edition in ("portable", "installed", "development")]
    assert len(set(locations)) == 3
    for cache in locations:
        assert not cache.exists()
        assert not open_pgn(FIXTURES / "mainline.pgn", cache_path=cache).from_cache
    for cache in locations:
        assert open_pgn(FIXTURES / "mainline.pgn", cache_path=cache, indexer=forbidden_index).from_cache


def test_source_changed_while_loading_saved_index_rebuilds(tmp_path, monkeypatch):
    from pgn_player.index_cache import IndexCache
    from pgn_player.pgn_loader import index_pgn
    source, cache = tmp_path / "games.pgn", tmp_path / "cache.sqlite3"
    source.write_text("1. e4 *\n")
    open_pgn(source, cache_path=cache)
    load = IndexCache.load
    old_index = None

    def replace_after_load(self, path, signature, cancel=None):
        nonlocal old_index
        saved = load(self, path, signature, cancel=cancel)
        old_index = weakref.ref(saved)
        source.write_text("1. d4 d5 *\n")
        return saved

    def rebuild_after_release(*args, **kwargs):
        assert old_index() is None, "Old index retained while building its replacement"
        return index_pgn(*args, **kwargs)

    monkeypatch.setattr(IndexCache, "load", replace_after_load)
    rebuilt = open_pgn(source, cache_path=cache, indexer=rebuild_after_release)
    assert not rebuilt.from_cache
    assert rebuilt.load_game(0).next().san() == "d4"


def test_invalid_cached_cookie_is_reported_for_worker_rebuild(tmp_path):
    cache = tmp_path / "cache.sqlite3"
    open_pgn(FIXTURES / "multiple_games.pgn", cache_path=cache)
    saved = open_pgn(FIXTURES / "multiple_games.pgn", cache_path=cache)
    info = saved.entries[2]
    saved.entries[2] = GameInfo(info.index, info.headers, 10 ** 40)
    with pytest.raises(CachedIndexError):
        saved.load_game(2)


def test_cached_headerless_result_is_not_mistaken_for_bad_offset(tmp_path):
    source, cache = tmp_path / "bare.pgn", tmp_path / "cache.sqlite3"
    source.write_text("1. e4 e5 1-0\n")
    open_pgn(source, cache_path=cache)
    saved = open_pgn(source, cache_path=cache, indexer=forbidden_index)
    assert saved.entries[0].headers == {}
    assert saved.load_game(0).headers["Result"] == "1-0"


def test_current_unchanged_index_is_shared_without_pgn_or_sqlite_reads(tmp_path, monkeypatch):
    from pgn_player.index_cache import IndexCache
    from pgn_player.pgn_loader import index_pgn
    source = (FIXTURES / "multiple_games.pgn").resolve()
    original = index_pgn(source)
    original.load_game(0)
    assert original._cache

    def forbidden_cache(*args, **kwargs):
        raise AssertionError("Current index reuse must not allocate another SQLite index")

    monkeypatch.setattr(IndexCache, "load", forbidden_cache)
    monkeypatch.setattr(Path, "open", forbidden_cache)
    phases, progress = [], []
    reused = open_pgn(source, cache_path=tmp_path / "absent-cache.sqlite3",
        current_collection=original, indexer=forbidden_index,
        status=phases.append, progress=progress.append)
    assert reused is not original
    assert reused.entries is original.entries
    assert reused.fingerprint == original.fingerprint and reused.encoding == original.encoding
    assert reused.from_cache and reused._cache == {} and reused.errors == {}
    assert phases == ["cache", "current"] and progress == []


@pytest.mark.parametrize("change", ["modified", "different", "forced"])
def test_current_index_reuse_requires_same_unchanged_file_and_cache_enabled(tmp_path, change):
    from pgn_player.pgn_loader import index_pgn
    source, cache = tmp_path / "one.pgn", tmp_path / "cache.sqlite3"
    source.write_text('[White "Before"]\n\n1. e4 *\n', encoding="utf-8")
    original = open_pgn(source, cache_path=cache)
    if change == "modified":
        source.write_text('[White "After"]\n\n1. d4 d5 *\n', encoding="utf-8")
    elif change == "different":
        source = tmp_path / "two.pgn"
        source.write_text('[White "Before"]\n\n1. e4 *\n', encoding="utf-8")
        # Matching metadata alone cannot authorize a different normalized path.
        stat = source.stat()
        original._signature = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
    scans = []

    def index(source, **kwargs):
        scans.append(source)
        return index_pgn(source, **kwargs)

    result = open_pgn(source, cache_path=cache, current_collection=original,
        indexer=index, use_cache=change != "forced")
    assert scans == [source.resolve()]
    assert result.entries is not original.entries
    assert not result.from_cache


def test_current_index_metadata_is_rechecked_before_reuse(tmp_path, monkeypatch):
    import pgn_player.pgn_loader as loader
    source, cache = tmp_path / "race.pgn", tmp_path / "cache.sqlite3"
    source.write_text("1. e4 *\n", encoding="utf-8")
    original = open_pgn(source, cache_path=cache)
    signature = loader._signature
    calls = 0

    def change_after_first_stat(path):
        nonlocal calls
        result = signature(path)
        calls += 1
        if calls == 1:
            source.write_text("1. d4 d5 *\n", encoding="utf-8")
        return result

    monkeypatch.setattr(loader, "_signature", change_after_first_stat)
    rebuilt = open_pgn(source, cache_path=cache, current_collection=original)
    assert not rebuilt.from_cache and rebuilt.entries is not original.entries
    assert rebuilt.load_game(0).next().san() == "d4"


def test_current_index_reuse_preserves_cancellation(tmp_path):
    from pgn_player.pgn_loader import index_pgn
    original = index_pgn(FIXTURES / "mainline.pgn")
    with pytest.raises(IndexingCancelled):
        open_pgn(original.path, cache_path=tmp_path / "cache.sqlite3",
            current_collection=original, cancel=lambda: True, indexer=forbidden_index)
