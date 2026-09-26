"""Offscreen checks for persistent-index loading in the existing player UI."""
from dataclasses import replace
import threading
import time

import chess
import pytest

from pgn_player.paths import resolve_paths
from pgn_player.storage import SettingsStore
from pgn_player.ui.main_window import MainWindow
import pgn_player.ui.main_window as window_module


PGN = '''[Event "Cache study"]
[White "Ada"]
[Black "Ben"]
[Result "1-0"]
[WhiteElo "2100"]
[ECO "C20"]
[DrillSet "Forks and pins"]

1. e4 {HIDDEN upcoming explanation} (1. d4 d5) e5 2. Nf3 Nc6 1-0

[Event "Second study"]
[White "Cora"]
[Black "Drew"]
[Result "1/2-1/2"]
[Opening "Queen pawn"]
[DrillSet "Endgame practice"]

1. d4 (1. c4 e5) d5 2. c4 1/2-1/2

[Event "Third study"]
[White "Erin"]
[Black "Finn"]
[Result "0-1"]

1. c4 e5 0-1
'''


@pytest.fixture
def ui_files(tmp_path):
    path = tmp_path / "study.pgn"
    path.write_text(PGN, encoding="utf-8")
    paths = resolve_paths("development", app_dir=tmp_path, data_override=tmp_path / "state")
    return path, paths


@pytest.fixture
def make_window(qtbot, ui_files):
    _, paths = ui_files
    windows = []

    def create(store=None):
        store = store or SettingsStore(paths.db_path)
        window = MainWindow(store, paths)
        qtbot.addWidget(window)
        window.show()
        windows.append((window, store))
        return window

    yield create
    for window, store in reversed(windows):
        if store._connection is not None:
            window.close()
            qtbot.waitUntil(lambda: window._worker is None, timeout=5000)
            qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
            store.close()


def wait_loaded(window, qtbot):
    qtbot.waitUntil(lambda: not window._loading and window._worker is None, timeout=10000)
    assert window.player.collection is not None, window._status_error


def record_status(window):
    messages = []
    window.statusBar().messageChanged.connect(
        lambda message: messages.append((message, window.progress.isVisible()))
    )
    return messages


def assert_search_metadata(window):
    games = window.game_list
    assert games.model.rowCount() == 3
    assert games.model.data(games.model.index(0, 4)) == "2100"
    assert games.model.data(games.model.index(0, 7)) == "C20"
    games.search.setText("forks pins")
    games.result.setCurrentText("1-0")
    assert games.proxy.rowCount() == 1
    games.result.setCurrentText("1/2-1/2")
    assert games.proxy.rowCount() == 0
    games.field.setCurrentText("Event")
    games.search.setText("second")
    assert games.proxy.rowCount() == 1
    games.field.setCurrentText("Opening / ECO")
    games.search.setText("queen")
    assert games.proxy.rowCount() == 1
    games.search.clear()
    games.result.setCurrentText("Any result")
    games.field.setCurrentText("All tags")


def test_restart_reuses_index_with_resume_and_search_metadata(make_window, ui_files, qtbot, monkeypatch):
    path, paths = ui_files
    first = make_window()
    first_messages = record_status(first)
    first.open_file(path)
    wait_loaded(first, qtbot)
    assert not first.player.collection.from_cache
    assert (paths.data_dir / "pgn-index-cache.sqlite3").is_file()
    assert any(message.startswith("Indexing ") and visible for message, visible in first_messages)
    assert_search_metadata(first)
    headers = [entry.headers.copy() for entry in first.player.collection.entries]
    fingerprint = first.player.collection.fingerprint
    first._navigate(lambda: first.player.select_game(1))
    first._navigate(lambda: first.player.go_to((1, 0)))
    first.flip_board()
    position = first.player.board.fen()
    first.close()
    qtbot.waitUntil(lambda: not first.isVisible())
    first.store.close()

    scans = []

    def unexpected_index(*args, **kwargs):
        scans.append(args)
        raise AssertionError("Unchanged PGN must reopen from its saved index")

    monkeypatch.setattr(window_module, "index_pgn", unexpected_index)
    reopened = make_window()
    messages = record_status(reopened)
    reopened.open_file(path)
    wait_loaded(reopened, qtbot)
    assert scans == []
    collection = reopened.player.collection
    assert collection.from_cache
    assert collection.fingerprint == fingerprint
    assert [entry.headers for entry in collection.entries] == headers
    assert reopened.player.game_index == 1
    assert reopened.player.path == (1, 0)
    assert reopened.player.board.fen() == position
    assert reopened.board.orientation == "black"
    assert len(collection._cache) == 2  # Initial and resumed game; third stays lazy.
    assert any(message == "Loading saved index…" and not visible for message, visible in messages)
    assert not any(message.startswith(("Indexing ", "Saving PGN index")) for message, _ in messages)
    assert_search_metadata(reopened)
    reopened._navigate(lambda: reopened.player.select_game(2))
    reopened._navigate(reopened.player.next)
    assert reopened.player.board.piece_at(chess.C4) == chess.Piece(chess.PAWN, chess.WHITE)
    assert not reopened.progress.isVisible()


def test_cached_reopen_preserves_active_guess_session(make_window, ui_files, qtbot, monkeypatch):
    path, _ = ui_files
    window = make_window()
    window.open_file(path)
    wait_loaded(window, qtbot)
    window.guess_color.setCurrentIndex(window.guess_color.findData("both"))
    window.guess_variations.setChecked(True)
    window._toggle_guess(True)
    window._board_move(chess.E2, chess.E4)
    assert window.guess.correct == 1

    def unexpected_index(*args, **kwargs):
        raise AssertionError("Cache hit should not invoke the full indexer")

    monkeypatch.setattr(window_module, "index_pgn", unexpected_index)
    window.open_file(path, restore=False)
    wait_loaded(window, qtbot)
    assert window.player.collection.from_cache
    assert window.player.path == ()
    assert window.guess.mode == "both"
    assert window.guess.include_variations
    assert window.guess_color.currentData() == "both"
    assert window.guess_button.isChecked()
    assert window.guess_panel.isVisible()
    assert window.board.input_enabled
    assert window.guess.correct == 1
    assert window.guess.incorrect == 0
    assert "e4" not in window.notation.toPlainText()
    assert "HIDDEN" not in window.notation.toPlainText()
    window._board_move(chess.D2, chess.D4)
    assert window.guess.correct == 2
    assert window.player.path == (1,)


def test_corrupt_cache_rebuilds_and_failed_open_keeps_loaded_view(make_window, ui_files, qtbot, monkeypatch):
    path, paths = ui_files
    window = make_window()
    window.open_file(path)
    wait_loaded(window, qtbot)
    (paths.data_dir / "pgn-index-cache.sqlite3").write_bytes(b"not a SQLite database")
    window.close()
    window = make_window(window.store)
    indexed = []
    real_index = window_module.index_pgn

    def count_index(source, **kwargs):
        indexed.append(source)
        return real_index(source, **kwargs)

    monkeypatch.setattr(window_module, "index_pgn", count_index)
    window.open_file(path)
    wait_loaded(window, qtbot)
    assert indexed == [path]
    assert not window.player.collection.from_cache
    assert len(window.player.collection) == 3
    window._navigate(lambda: window.player.select_game(1))
    window._navigate(window.player.next)
    collection = window.player.collection
    position = window.player.board.fen()
    title = window.windowTitle()
    window.open_file(path.with_name("missing.pgn"))
    wait_loaded(window, qtbot)
    assert "Could not open PGN" in window._status_error
    assert window.player.collection is collection
    assert window.player.game_index == 1
    assert window.player.path == (0,)
    assert window.player.board.fen() == position
    assert window.windowTitle() == title
    assert window.game_list.model.rowCount() == 3


def test_bad_offset_discovered_lazily_rebuilds_in_worker(make_window, ui_files, qtbot, monkeypatch):
    path, _ = ui_files
    window = make_window()
    window.open_file(path)
    wait_loaded(window, qtbot)
    window.open_file(path, restore=False)
    wait_loaded(window, qtbot)
    collection = window.player.collection
    assert collection.from_cache
    # The first game is already loaded. Corruption in a later cached record is
    # discovered only when navigation asks the lazy loader for that game.
    collection.entries[1] = replace(collection.entries[1], offset=path.stat().st_size + 100)
    indexed = []
    real_index = window_module.index_pgn

    def count_index(source, **kwargs):
        indexed.append(source)
        return real_index(source, **kwargs)

    monkeypatch.setattr(window_module, "index_pgn", count_index)
    window._navigate(window.player.next_game)
    wait_loaded(window, qtbot)
    assert indexed == [path]
    assert window.player.collection is not collection
    assert not window.player.collection.from_cache
    window._navigate(lambda: window.player.select_game(1))
    assert window.player.game_index == 1
    assert "Cora" in window.players_label.text()


def test_bad_cached_resume_offset_rebuilds_and_retains_saved_target(make_window, ui_files, qtbot, monkeypatch):
    from pgn_player.index_cache import IndexCache

    path, _ = ui_files
    window = make_window()
    window.open_file(path)
    wait_loaded(window, qtbot)
    window._navigate(lambda: window.player.select_game(1))
    window._navigate(lambda: window.player.go_to((1, 0)))
    window.flip_board()
    window._save_resume()
    position = window.player.board.fen()
    window.close()
    window = make_window(window.store)
    real_load = IndexCache.load
    real_index = window_module.index_pgn
    indexed = []

    def bad_resume_offset(cache, source, signature, *, cancel=None):
        saved = real_load(cache, source, signature, cancel=cancel)
        assert saved is not None
        saved.entries[1] = replace(saved.entries[1], offset=saved.entries[0].offset)
        return saved

    def count_index(source, **kwargs):
        indexed.append(source)
        return real_index(source, **kwargs)

    monkeypatch.setattr(IndexCache, "load", bad_resume_offset)
    monkeypatch.setattr(window_module, "index_pgn", count_index)
    window.open_file(path)
    wait_loaded(window, qtbot)
    assert indexed == [path]
    assert not window.player.collection.from_cache
    assert window.player.game_index == 1
    assert window.player.path == (1, 0)
    assert window.player.board.fen() == position
    assert window.board.orientation == "black"
    assert "Could not restore" not in window._status_error


@pytest.mark.parametrize("operation", ["cancel", "close"])
def test_cache_load_cancellation_and_close_are_safe(make_window, ui_files, qtbot, monkeypatch, operation):
    from pgn_player.index_cache import CacheCancelled, IndexCache

    path, _ = ui_files
    window = make_window()
    window.open_file(path)
    wait_loaded(window, qtbot)
    old_collection = window.player.collection
    entered = threading.Event()

    def slow_cache_load(self, source, signature, *, cancel=None):
        entered.set()
        while not cancel():
            time.sleep(.005)
        raise CacheCancelled("Canceled")

    monkeypatch.setattr(IndexCache, "load", slow_cache_load)
    messages = record_status(window)
    # A different file must consult SQLite; the current unchanged file can
    # safely reuse its already loaded index without an asynchronous DB read.
    another_path = path.with_name("another-study.pgn")
    another_path.write_text(PGN, encoding="utf-8")
    window.open_file(another_path)
    qtbot.waitUntil(entered.is_set, timeout=3000)
    assert window._loading
    assert not window.progress.isVisible()
    if operation == "cancel":
        window._cancel_loading()
        wait_loaded(window, qtbot)
        assert window.isVisible()
        assert window.player.collection is old_collection
        assert not window.cancel_load.isVisible()
    else:
        window.close()
        qtbot.waitUntil(lambda: window._worker is None, timeout=3000)
        qtbot.waitUntil(lambda: not window.isVisible(), timeout=3000)
    assert not any(message.startswith("Indexing ") for message, _ in messages)
    assert path.read_text(encoding="utf-8") == PGN
