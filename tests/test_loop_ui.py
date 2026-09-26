"""Offscreen integration checks for the persistent Loop playback option."""
import chess
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from pgn_player.paths import resolve_paths
from pgn_player.storage import SettingsStore
from pgn_player.ui.main_window import MainWindow
from pgn_player.ui.preferences import PreferencesDialog


PGN = '''[Event "First study"]
[White "Ada"]
[Black "Ben"]
[Result "*"]

1. e4 e5 2. Nf3 *

[Event "Second study"]
[White "Cora"]
[Black "Drew"]
[Result "*"]

1. d4 d5 2. c4 *
'''


@pytest.fixture
def make_window(qtbot, tmp_path):
    paths = resolve_paths("development", app_dir=tmp_path, data_override=tmp_path / "state")
    source = tmp_path / "studies.pgn"
    source.write_text(PGN, encoding="utf-8")
    windows = []

    def create():
        store = SettingsStore(paths.db_path)
        window = MainWindow(store, paths)
        windows.append((window, store))
        qtbot.addWidget(window)
        window.show()
        window.open_file(source, restore=False)
        qtbot.waitUntil(lambda: window._worker is None and not window._loading, timeout=10000)
        assert window.player.collection is not None, window._status_error
        return window

    yield create
    for window, store in reversed(windows):
        if store._connection is not None:
            window.close()
            qtbot.waitUntil(lambda: window._worker is None, timeout=5000)
            qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
            store.close()


def fire_playback_timer(window):
    # A real single-shot timer is inactive when its timeout handler runs. Stop
    # it before driving the handler so timing assertions need no wall-clock wait.
    assert window._play_timer.isActive()
    window._play_timer.stop()
    window._autoplay_tick()


def test_loop_checkbox_defaults_off_and_survives_restart(make_window, qtbot):
    window = make_window()
    assert not window.loop_box.isChecked()
    assert not window.autoplay.loop
    assert window.store.get_settings()["loop"] is False
    qtbot.mouseClick(window.loop_box, Qt.MouseButton.LeftButton)
    assert window.loop_box.isChecked()
    assert window.autoplay.loop
    assert window.store.get("loop") is True
    assert not window.autoplay.playing
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    window.store.close()

    reopened = make_window()
    assert reopened.loop_box.isChecked()
    assert reopened.autoplay.loop
    assert reopened.settings["loop"] is True
    assert not reopened.autoplay.playing
    qtbot.mouseClick(reopened.loop_box, Qt.MouseButton.LeftButton)
    assert not reopened.autoplay.loop
    assert reopened.store.get("loop") is False


@pytest.mark.parametrize("initial, requested", [(False, True), (True, False)])
def test_preferences_loop_stays_in_sync_with_checkbox_and_storage(make_window, monkeypatch, initial, requested):
    window = make_window()
    window.loop_box.setChecked(initial)
    shown = []

    def accept_preferences(dialog):
        shown.append(dialog)
        assert dialog.loop.isChecked() is initial
        assert dialog.values()["loop"] is initial
        dialog.loop.setChecked(requested)
        dialog.continue_next.setChecked(False)
        dialog.between.setValue(1.5)
        assert dialog.values()["loop"] is requested
        return QDialog.DialogCode.Accepted

    # Exercise the actual dialog's controls and accepted-value path without
    # opening a modal window or entering a nested interactive event loop.
    monkeypatch.setattr(PreferencesDialog, "exec", accept_preferences)
    window.show_preferences()
    assert len(shown) == 1
    assert window.loop_box.isChecked() is requested
    assert window.autoplay.loop is requested
    assert window.settings["loop"] is requested
    assert window.store.get("loop") is requested
    assert not window.continue_box.isChecked()
    assert not window.autoplay.continue_next
    assert window.autoplay.between_games_seconds == 1.5


def test_cancelled_preferences_does_not_change_loop(make_window, monkeypatch):
    window = make_window()
    window.loop_box.setChecked(True)

    def reject_preferences(dialog):
        dialog.loop.setChecked(False)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(PreferencesDialog, "exec", reject_preferences)
    window.show_preferences()
    assert window.loop_box.isChecked()
    assert window.autoplay.loop
    assert window.store.get("loop") is True


@pytest.mark.parametrize("continue_next, target_game, first_move", [
    (False, 1, "d2d4"),
    (True, 0, "e2e4"),
])
def test_loop_timer_uses_game_pause_then_move_delay_without_skipping(make_window, continue_next, target_game, first_move):
    window = make_window()
    window.loop_box.setChecked(True)
    window.continue_box.setChecked(continue_next)
    window.speed.setValue(.6)
    window.autoplay.between_games_seconds = 1.75
    window._navigate(lambda: window.player.select_game(1))
    window._navigate(lambda: window.player.go_to((0, 0)))
    window.toggle_play()
    assert window._play_timer.interval() == 600

    fire_playback_timer(window)
    assert window.player.path == (0, 0, 0)
    assert window.player.at_end
    assert window.autoplay.playing
    assert window._play_timer.interval() == 1750
    final_position = window.player.board.fen()
    window.toggle_play()
    assert not window._play_timer.isActive()
    assert not window.autoplay.playing
    window._autoplay_tick()
    assert window.player.board.fen() == final_position
    window.toggle_play()
    assert window._play_timer.interval() == 1750

    fire_playback_timer(window)
    assert window.player.game_index == target_game
    assert window.player.path == ()
    assert window.player.board == chess.Board()
    assert window.autoplay.playing
    assert window._play_timer.interval() == 600

    window.toggle_play()
    assert not window._play_timer.isActive()
    window._autoplay_tick()
    assert window.player.path == ()
    window.toggle_play()
    assert window._play_timer.interval() == 600
    fire_playback_timer(window)
    assert window.player.path == (0,)
    assert window.player.board.peek() == chess.Move.from_uci(first_move)
    assert window._play_timer.interval() == 600


def test_loop_and_continue_toggles_preserve_guess_mode_and_block_autoplay(make_window, qtbot):
    window = make_window()
    window.guess_color.setCurrentIndex(window.guess_color.findData("both"))
    window.guess_variations.setChecked(True)
    window._toggle_guess(True)
    window._board_move(chess.D2, chess.D4)
    assert window.guess.incorrect == 1
    assert window.guess.pending_answer == "e4"
    feedback = window.guess_feedback.text()
    for checked in (True, False, True):
        window.loop_box.setChecked(checked)
        window.continue_box.setChecked(checked)
        assert window.guess.mode == "both"
        assert window.guess.include_variations
        assert window.guess.guesses == 1
        assert window.guess.pending_answer == "e4"
        assert window.guess_feedback.text() == feedback
        assert window.guess_button.isChecked()
        assert window.guess_panel.isVisible()
        assert window.guess_continue.isVisible()
        assert not window.play_button.isEnabled()
        window.toggle_play()
        assert not window.autoplay.playing
        assert not window._play_timer.isActive()
        assert window.player.path == ()
        assert "e4" not in window.notation.toPlainText()

    qtbot.mouseClick(window.guess_button, Qt.MouseButton.LeftButton)
    assert window.guess.mode == "off"
    assert window.loop_box.isChecked()
    window.toggle_play()
    assert window.autoplay.playing
    assert window._play_timer.isActive()
