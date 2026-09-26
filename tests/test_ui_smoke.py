"""Background Qt checks for the user workflows that join UI and controllers."""
import time

import chess
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from pgn_player.paths import resolve_paths
from pgn_player.storage import SettingsStore
from pgn_player.ui.main_window import MainWindow


PGN = '''[Event "UI test"]
[White "Ada"]
[Black "Ben"]
[Result "*"]

1. e4 {SECRET upcoming comment} (1. d4 d5) e5 2. Nf3 Nc6 *

[Event "Second event"]
[White "Cora"]
[Black "Drew"]
[Result "1/2-1/2"]

1. d4 d5 1/2-1/2
'''


@pytest.fixture
def window(qtbot, tmp_path):
    paths = resolve_paths("development", app_dir=tmp_path, data_override=tmp_path / "state")
    store = SettingsStore(paths.db_path)
    window = MainWindow(store, paths)
    qtbot.addWidget(window)
    window.show()
    path = tmp_path / "study.pgn"
    path.write_text(PGN, encoding="utf-8")
    window.open_file(path)
    qtbot.waitUntil(lambda: not window._loading, timeout=10000)
    assert window.player.collection is not None, window._status_error
    yield window
    window.close()
    qtbot.waitUntil(lambda: window._worker is None, timeout=10000)
    store.close()


def test_launch_open_navigation_theme_and_render(window, qtbot):
    assert len(window.player.collection) == 2
    assert window.player.game_index == 0
    assert window.player.path == ()
    assert window.game_list.model.rowCount() == 2
    assert "Ada" in window.players_label.text()
    qtbot.mouseClick(window.next_button, Qt.MouseButton.LeftButton)
    assert window.player.path == (0,)
    assert window.board.board.piece_at(chess.E4).symbol() == "P"
    assert "SECRET" in window.comments.toPlainText()
    window.notation.move_selected.emit((1, 0))
    assert window.player.path == (1, 0)
    assert window.mainline_button.isEnabled()
    qtbot.mouseClick(window.mainline_button, Qt.MouseButton.LeftButton)
    assert window.player.path == (0, 0)
    qtbot.mouseClick(window.next_game_button, Qt.MouseButton.LeftButton)
    assert window.player.game_index == 1
    assert "Cora" in window.players_label.text()
    window.toggle_theme()
    assert window.theme == "dark"
    assert window.store.get("theme") == "dark"
    window.flip_board()
    assert window.board.orientation == "black"
    image = window.board.grab().toImage()
    assert not image.isNull()
    assert image.pixelColor(image.width() // 3, image.height() // 3) != image.pixelColor(0, 0)


def test_search_and_result_filter(window):
    window.game_list.search.setText("Cora")
    assert window.game_list.proxy.rowCount() == 1
    window.game_list.result.setCurrentText("1-0")
    assert window.game_list.proxy.rowCount() == 0
    window.game_list.result.setCurrentText("1/2-1/2")
    assert window.game_list.proxy.rowCount() == 1
    window.game_list.search.clear()
    window.game_list.result.setCurrentText("Any result")
    window.game_list.field.setCurrentText("Event")
    window.game_list.search.setText("second")
    assert window.game_list.proxy.rowCount() == 1


def test_guess_hides_answers_and_correct_opponent_reply(window, qtbot):
    qtbot.mouseClick(window.guess_button, Qt.MouseButton.LeftButton)
    assert window.guess.mode == "white"
    assert "e4" not in window.notation.toPlainText()
    assert "SECRET" not in window.comments.toPlainText()
    assert "SECRET" not in window.notation.toPlainText()
    window._board_move(chess.E2, chess.E4)
    assert window.guess.correct == 1
    assert window.player.path == (0,)
    assert "e5" not in window.notation.toPlainText()
    qtbot.waitUntil(lambda: window.player.path == (0, 0), timeout=3000)
    assert window.guess.waiting_for_guess
    assert "Nf3" not in window.notation.toPlainText()


def test_wrong_guess_requires_continue_and_navigation_cancels_reply(window, qtbot):
    window._toggle_guess(True)
    window._board_move(chess.D2, chess.D4)
    assert window.guess.incorrect == 1
    assert window.player.path == ()
    assert "recorded move is e4" in window.guess_feedback.text()
    assert not window._opponent_timer.isActive()
    assert window.guess_continue.isVisible()
    qtbot.mouseClick(window.guess_continue, Qt.MouseButton.LeftButton)
    assert window.player.path == (0,)
    assert window._opponent_timer.isActive()
    window._navigate(window.player.first)
    assert not window._opponent_timer.isActive()
    assert window.guess.mode == "off"
    qtbot.wait(750)
    assert window.player.path == ()


def test_board_click_input_and_orientation(window, qtbot):
    window._toggle_guess(True)
    for square in (chess.E2, chess.E4):
        qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton, pos=window.board.square_rect(square).center().toPoint())
    assert window.guess.correct == 1
    window._stop_modes()
    window.flip_board()
    for square in (chess.A1, chess.H8, chess.D4):
        assert window.board.square_at(window.board.square_rect(square).center()) == square


def test_autoplay_cancel_and_next_game(window, qtbot):
    window.speed.setValue(.1)
    window.autoplay.between_games_seconds = .05
    window.toggle_play()
    qtbot.waitUntil(lambda: window.player.game_index == 1, timeout=3000)
    window._navigate(window.player.first)
    assert not window._play_timer.isActive()
    assert not window.autoplay.playing
    qtbot.wait(250)
    assert window.player.path == ()
    window.toggle_play()
    qtbot.waitUntil(lambda: not window.autoplay.playing, timeout=3000)
    assert window.player.path == (0, 0)


def test_resume_and_failed_open_preserve_original(window, qtbot, tmp_path):
    path = window.player.collection.path
    original = path.read_bytes()
    window._navigate(lambda: window.player.select_game(1))
    window._navigate(window.player.next)
    window.flip_board()
    window._save_resume()
    window.open_file(path)
    qtbot.waitUntil(lambda: not window._loading, timeout=10000)
    assert window.player.game_index == 1
    assert window.player.path == (0,)
    assert window.board.orientation == "black"
    window.open_file(tmp_path / "missing.pgn")
    qtbot.waitUntil(lambda: not window._loading, timeout=10000)
    assert "Could not open" in window._status_error
    assert window.player.game_index == 1
    assert path.read_bytes() == original


def test_close_cancels_inflight_worker(qtbot, tmp_path, monkeypatch):
    from pgn_player.pgn_loader import IndexingCancelled
    import pgn_player.ui.main_window as module

    def slow_index(path, progress=None, cancel=None):
        while not cancel():
            time.sleep(.005)
        raise IndexingCancelled("Canceled")

    monkeypatch.setattr(module, "index_pgn", slow_index)
    paths = resolve_paths("development", app_dir=tmp_path, data_override=tmp_path / "state")
    store = SettingsStore(paths.db_path)
    window = MainWindow(store, paths)
    qtbot.addWidget(window)
    window.show()
    window.open_file(tmp_path / "large.pgn")
    assert window._loading
    window.close()
    qtbot.waitUntil(lambda: window._worker is None, timeout=3000)
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=3000)
    store.close()


def test_empty_file_preserves_loaded_view(window, qtbot, tmp_path):
    old_collection = window.player.collection
    old_title = window.windowTitle()
    empty = tmp_path / "empty.pgn"
    empty.write_text("", encoding="utf-8")
    window.open_file(empty)
    qtbot.waitUntil(lambda: not window._loading, timeout=5000)
    assert window.player.collection is old_collection
    assert window.windowTitle() == old_title
    assert window.game_list.model.rowCount() == 2
    assert "no readable" in window._status_error


def test_storage_failure_does_not_leave_partial_loaded_view(window, qtbot, tmp_path, monkeypatch):
    def unavailable(*args):
        raise RuntimeError("Database temporarily unavailable")

    monkeypatch.setattr(window.store, "load_resume", unavailable)
    monkeypatch.setattr(window.store, "add_recent", unavailable)
    path = tmp_path / "another.pgn"
    path.write_text('[White "New player"]\n[Black "Other"]\n\n1. d4 *\n', encoding="utf-8")
    window.open_file(path)
    qtbot.waitUntil(lambda: not window._loading, timeout=5000)
    assert window.player.collection.path == path
    assert "New player" in window.players_label.text()
    assert "another.pgn" in window.windowTitle()
    assert window.game_list.model.rowCount() == 1
    assert "Could not save the recent file" in window._status_error


def test_export_preserves_source_and_variations(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    import chess.pgn

    source = window.player.collection.path
    original = source.read_bytes()
    destination = tmp_path / "export.pgn"
    window.toggle_play()
    assert window.autoplay.playing

    def choose_destination(*args):
        assert not window.autoplay.playing
        assert not window._play_timer.isActive()
        assert not window._opponent_timer.isActive()
        return str(destination), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_destination)
    window.export_game()
    with destination.open(encoding="utf-8") as stream:
        game = chess.pgn.read_game(stream)
    assert game.headers["White"] == "Ada"
    assert len(game.variations) == 2
    assert game.variations[0].comment == "SECRET upcoming comment"
    assert source.read_bytes() == original
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(source), ""))
    window.export_game()
    assert source.read_bytes() == original
    assert "open PGN is preserved" in window._status_error


def test_notation_escapes_html_and_text_entry_does_not_navigate(window, qtbot):
    window.player.node.comment = '<img src="https://invalid.example/private"> & <b>literal</b>'
    window._refresh()
    assert '<img src="https://invalid.example/private">' in window.notation.toPlainText()
    assert "&lt;img" in window.notation.toHtml()
    window.game_list.search.setFocus()
    qtbot.keyClicks(window.game_list.search, "F")
    qtbot.keyClick(window.game_list.search, Qt.Key.Key_Right)
    qtbot.keyClick(window.game_list.search, Qt.Key.Key_Space)
    assert window.board.orientation == "white"
    assert window.player.path == ()
    assert not window.autoplay.playing
