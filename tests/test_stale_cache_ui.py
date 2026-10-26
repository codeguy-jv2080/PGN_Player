"""Headless saved-game identity checks; no live desktop or real PGNs."""

import os
import sqlite3

import pytest

from pgn_player.paths import resolve_paths
from pgn_player.storage import SettingsStore
from pgn_player.ui.main_window import MainWindow
import pgn_player.ui.main_window as window_module


def _large_fixture(tmp_path):
    from pgn_player.source_validation import sample_ranges

    source = tmp_path / "study.pgn"
    records = [
        f'[Event "Study {index:03d}"]\n[Result "*"]\n\n'
        '1. e4 e5 2. Nf3 Nc6 {' + "A" * 6000 + '} *\n\n'
        for index in range(100)
    ]
    raw = "".join(records).encode("ascii")
    source.write_bytes(raw)
    samples = sample_ranges(len(raw))
    offset = 0
    for index, record in enumerate(records):
        move_offset = offset + record.index("1. e4 e5")
        move_end = move_offset + len("1. e4 e5")
        if index and all(move_end <= start or move_offset >= start + length for start, length in samples):
            return source, index, move_offset
        offset += len(record)
    raise AssertionError("Synthetic fixture must include moves outside sample windows")


def _change_unsampled_moves(source, offset):
    from pgn_player.source_validation import source_sample

    stat = source.stat()
    before = source_sample(source, stat.st_size)
    with source.open("r+b") as stream:
        stream.seek(offset)
        assert stream.read(8) == b"1. e4 e5"
        stream.seek(offset)
        stream.write(b"1. d4 d5")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert source_sample(source, stat.st_size) == before
    current = source.stat()
    assert (current.st_size, current.st_mtime_ns, current.st_ino) == (
        stat.st_size, stat.st_mtime_ns, stat.st_ino,
    )
    return source.read_bytes()


@pytest.fixture
def make_window(tmp_path, qtbot):
    paths = resolve_paths("development", app_dir=tmp_path, data_override=tmp_path / "profile")
    windows = []

    def make():
        store = SettingsStore(paths.db_path)
        window = MainWindow(store, paths)
        qtbot.addWidget(window)
        window.show()
        windows.append((window, store))
        return window

    yield make
    for window, store in reversed(windows):
        if store._connection is not None:
            window.close()
            qtbot.waitUntil(lambda: window._worker is None, timeout=10000)
            store.close()


def _wait(window, qtbot):
    qtbot.waitUntil(lambda: not window._loading and window._worker is None, timeout=15000)
    assert window.player.collection is not None, window._status_error


@pytest.mark.parametrize("same_window", [False, True], ids=["restart", "same-window"])
def test_saved_game_change_outside_samples_rebuilds_before_restoring_move(tmp_path, qtbot, monkeypatch, make_window, same_window):
    source, selected, offset = _large_fixture(tmp_path)
    first = make_window()
    first.open_file(source)
    _wait(first, qtbot)
    first._navigate(lambda: first.player.select_game(selected))
    first._navigate(lambda: first.player.go_to((0, 0, 0)))
    first.flip_board()
    first._save_resume()
    old_fingerprint = first.player.collection.fingerprint
    saved = first.store.load_resume(source, old_fingerprint)
    assert saved["game_digest"] == first.player.game._source_digest
    assert saved["variation_path"] == [0, 0, 0]
    if not same_window:
        first.close()
        first.store.close()
    changed = _change_unsampled_moves(source, offset)
    indexed = []
    native_index = window_module.index_pgn

    def counted_index(path, **kwargs):
        indexed.append(path)
        return native_index(path, **kwargs)

    monkeypatch.setattr(window_module, "index_pgn", counted_index)
    reopened = first if same_window else make_window()
    reopened.open_file(source)
    _wait(reopened, qtbot)
    assert indexed == [source.resolve()]
    assert reopened.player.collection.fingerprint != old_fingerprint
    assert reopened.player.game_index == 0 and reopened.player.path == ()
    assert reopened.player.collection.load_game(selected).next().san() == "d4"
    assert source.read_bytes() == changed


def test_saving_displayed_game_after_unsampled_edit_keeps_its_original_identity(tmp_path, qtbot, make_window):
    source, selected, offset = _large_fixture(tmp_path)
    window = make_window()
    window.open_file(source)
    _wait(window, qtbot)
    window._navigate(lambda: window.player.select_game(selected))
    window._navigate(window.player.next)
    window._save_resume()
    collection_fingerprint = window.player.collection.fingerprint
    original_digest = window.player.game._source_digest
    changed = _change_unsampled_moves(source, offset)
    window._save_resume()
    saved = window.store.load_resume(source, collection_fingerprint)
    # Never attach the replacement file's identity to the old displayed board.
    assert saved["game_digest"] == original_digest
    assert saved["variation_path"] == [0]
    assert window.player.node.san() == "e4"
    assert source.read_bytes() == changed


def test_legacy_resume_gains_identity_after_a_fresh_index_without_resetting_preferences(tmp_path, qtbot, monkeypatch, make_window):
    source, selected, _ = _large_fixture(tmp_path)
    first = make_window()
    first.open_file(source)
    _wait(first, qtbot)
    fingerprint = first.player.collection.fingerprint
    first.close()
    first.store.save_resume(source, fingerprint, selected, (0, 0), "black")
    first.store.set("theme", "dark")
    first.store.close()
    with sqlite3.connect(first.paths.data_dir / "pgn-index-cache.sqlite3") as db:
        db.execute("UPDATE collections SET runtime_id='legacy-parser-without-content-validation'")
    indexed = []
    native_index = window_module.index_pgn

    def count_index(path, **kwargs):
        indexed.append(path)
        return native_index(path, **kwargs)

    monkeypatch.setattr(window_module, "index_pgn", count_index)
    reopened = make_window()
    reopened.open_file(source)
    _wait(reopened, qtbot)
    assert not reopened.player.collection.from_cache and indexed == [source.resolve()]
    assert reopened.player.game_index == selected and reopened.player.path == (0, 0)
    assert reopened.player.node.san() == "e5" and reopened.board.orientation == "black"
    assert reopened.theme == "dark"
    saved = reopened.store.load_resume(source, fingerprint)
    assert saved["game_digest"] == reopened.player.game._source_digest


def test_legacy_resume_on_a_cache_hit_is_upgraded_once_and_preserved(tmp_path, qtbot, monkeypatch, make_window):
    source, selected, _ = _large_fixture(tmp_path)
    first = make_window()
    first.open_file(source)
    _wait(first, qtbot)
    fingerprint = first.player.collection.fingerprint
    first.close()
    first.store.save_resume(source, fingerprint, selected, (0, 0), "black")
    first.store.set("theme", "dark")
    first.store.close()

    indexed = []
    native_index = window_module.index_pgn

    def count_index(path, **kwargs):
        indexed.append(path)
        assert len(indexed) == 1, "An upgraded resume must not require recurring full-file scans"
        return native_index(path, **kwargs)

    monkeypatch.setattr(window_module, "index_pgn", count_index)
    reopened = make_window()
    reopened.open_file(source)
    _wait(reopened, qtbot)
    assert indexed == [source.resolve()]
    assert not reopened.player.collection.from_cache
    assert reopened.player.game_index == selected and reopened.player.path == (0, 0)
    assert reopened.player.node.san() == "e5" and reopened.board.orientation == "black"
    assert reopened.theme == "dark"
    saved = reopened.store.load_resume(source, fingerprint)
    assert saved["game_digest"] == reopened.player.game._source_digest
    assert saved["game_index"] == selected and saved["variation_path"] == [0, 0]

    reopened.open_file(source)
    _wait(reopened, qtbot)
    assert indexed == [source.resolve()]
    assert reopened.player.collection.from_cache
    assert reopened.player.game_index == selected and reopened.player.path == (0, 0)
    assert reopened.player.node.san() == "e5" and reopened.board.orientation == "black"


def test_missing_saved_variation_path_stays_at_game_start(tmp_path, qtbot, make_window):
    source, selected, _ = _large_fixture(tmp_path)
    first = make_window()
    first.open_file(source)
    _wait(first, qtbot)
    first._navigate(lambda: first.player.select_game(selected))
    fingerprint = first.player.collection.fingerprint
    digest = first.player.game._source_digest
    first.close()
    first.store.save_resume(source, fingerprint, selected, (0, 99), "black", game_digest=digest)
    first.store.close()
    reopened = make_window()
    reopened.open_file(source)
    _wait(reopened, qtbot)
    assert reopened.player.game_index == selected and reopened.player.path == ()
    assert reopened.player.board.fen() == reopened.player.game.board().fen()
    assert "saved" in reopened._status_error.lower()


def test_wrong_saved_digest_rebuilds_once_then_skips_resume(tmp_path, qtbot, monkeypatch, make_window):
    source, selected, _ = _large_fixture(tmp_path)
    raw = source.read_bytes()
    first = make_window()
    first.open_file(source)
    _wait(first, qtbot)
    fingerprint = first.player.collection.fingerprint
    first.close()
    first.store.save_resume(source, fingerprint, selected, (0, 0), "black", game_digest="0" * 64)
    first.store.close()
    indexed = []
    native_index = window_module.index_pgn

    def count_index(path, **kwargs):
        indexed.append(path)
        assert len(indexed) == 1, "A mismatched saved digest must not repeatedly rebuild an unchanged PGN"
        return native_index(path, **kwargs)

    monkeypatch.setattr(window_module, "index_pgn", count_index)
    reopened = make_window()
    reopened.open_file(source)
    _wait(reopened, qtbot)
    assert indexed == [source.resolve()]
    assert not reopened.player.collection.from_cache
    assert not reopened.player.collection.invalid
    assert reopened.player.collection.fingerprint == fingerprint
    assert reopened.player.game_index == 0 and reopened.player.path == ()
    assert "saved" in reopened._status_error.lower()

    # The newly loaded beginning now supplies a valid identity. Reopening it
    # reuses the valid index, rather than cycling through fresh scans again.
    reopened.open_file(source)
    _wait(reopened, qtbot)
    assert indexed == [source.resolve()]
    assert reopened.player.collection.from_cache
    assert not reopened.player.collection.invalid
    assert reopened.player.game_index == 0 and reopened.player.path == ()
    assert source.read_bytes() == raw


def test_next_game_rebuilds_changed_source_without_overwriting_invalid_resume(tmp_path, qtbot, monkeypatch, make_window):
    source = tmp_path / "changed-while-open.pgn"
    source.write_text('[Event "First"]\n\n1. e4 e5 2. Nf3 Nc6 *\n\n'
                      '[Event "Other"]\n\n1. d4 d5 2. c4 e6 *\n', encoding="utf-8")
    window = make_window()
    window.open_file(source)
    _wait(window, qtbot)
    window._navigate(window.player.next)
    window._save_resume()
    original = window.player.collection
    saved = window.store.load_resume(source, original.fingerprint)
    assert saved["variation_path"] == [0]
    # Keep the previously persisted route distinct from the displayed route,
    # and suppress only the debounce timer to inspect the retry's own writes.
    window._save_timer.stop()
    monkeypatch.setattr(window._save_timer, "start", lambda *args: None)
    window._navigate(window.player.next)
    assert window.player.path == (0, 0)
    changed = source.read_bytes().replace(b"1. e4 e5", b"1. d4 d5", 1)
    source.write_bytes(changed)
    writes, indexed = [], []
    native_save, native_index = window.store.save_resume, window_module.index_pgn

    def record_save(*args, **kwargs):
        writes.append((args, kwargs))
        return native_save(*args, **kwargs)

    def count_index(path, **kwargs):
        indexed.append(path)
        return native_index(path, **kwargs)

    monkeypatch.setattr(window.store, "save_resume", record_save)
    monkeypatch.setattr(window_module, "index_pgn", count_index)
    window._navigate(window.player.next_game)
    assert original.invalid
    _wait(window, qtbot)
    assert indexed == [source.resolve()]
    assert writes == [], "A known-invalid displayed collection must not overwrite the historical resume"
    assert window.store.load_resume(source, original.fingerprint) == saved
    assert window.player.collection.fingerprint != original.fingerprint
    assert not window.player.collection.invalid
    assert window.player.game_index == 0 and window.player.path == ()
    assert window.player.game.next().san() == "d4"
    assert source.read_bytes() == changed
