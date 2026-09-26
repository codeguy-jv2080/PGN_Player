"""Offscreen coverage of the notation-only font menu and keyboard shortcuts."""
import chess
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QPushButton, QTextEdit, QVBoxLayout

from pgn_player.paths import resolve_paths
from pgn_player.storage import SettingsStore
from pgn_player.ui.main_window import MainWindow
from pgn_player.ui.themes import COLORS
import pgn_player.ui.main_window as window_module


PGN = '''[Event "Font study"]
[White "Ada"]
[Black "Ben"]
[Result "*"]

1. e4! {SECRET inline comment} (1. d4 d5 2. c4) e5 2. Nf3 Nc6 *
'''
SIZES = (10, 12, 14, 16, 18)


@pytest.fixture
def make_window(qtbot, tmp_path):
    paths = resolve_paths("development", app_dir=tmp_path, data_override=tmp_path / "state")
    source = tmp_path / "study.pgn"
    source.write_text(PGN, encoding="utf-8")
    windows = []

    def create():
        store = SettingsStore(paths.db_path)
        window = MainWindow(store, paths)
        windows.append((window, store))
        qtbot.addWidget(window)
        window.show()
        window.open_file(source, restore=False)
        qtbot.waitUntil(lambda: not window._loading and window._worker is None, timeout=10000)
        assert window.player.collection is not None, window._status_error
        return window

    yield create
    for window, store in reversed(windows):
        if store._connection is not None:
            window.close()
            qtbot.waitUntil(lambda: window._worker is None, timeout=5000)
            qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
            store.close()


def assert_selected_size(window, size):
    assert window.notation.font_size == size
    assert window.settings["notation_font_size"] == size
    assert window.notation_font_group.isExclusive()
    assert sorted(window.notation_font_actions) == list(SIZES)
    assert [value for value, action in window.notation_font_actions.items() if action.isChecked()] == [size]


def position_snapshot(window):
    player = window.player
    return (player.collection, player.game, player.node, player.board,
            player.game_index, player.path, player.board.fen())


def assert_same_position(window, snapshot):
    current = position_snapshot(window)
    assert all(before is after for before, after in zip(snapshot[:4], current[:4]))
    assert current[4:] == snapshot[4:]


def other_fonts(window):
    return [widget.font().toString() for widget in (
        window.board, window.players_label, window.metadata, window.game_list.table,
        window.game_list.search, window.play_button, window.guess_button, window.speed,
        window.menuBar(), window.statusBar(), window.guess_panel, window.guess_color,
        window.guess_feedback, window.guess_stats,
    )]


def activate_offscreen(window, widget, qtbot, qapp):
    assert qapp.platformName() == "offscreen"
    qapp.setActiveWindow(window)
    widget.setFocus()
    qtbot.waitUntil(lambda: window.isActiveWindow() and
                   (qapp.focusWidget() is widget or widget.isAncestorOf(qapp.focusWidget())))


@pytest.mark.parametrize("size", SIZES)
def test_each_font_menu_choice_is_live_and_persists_without_reloading(make_window, qtbot, monkeypatch, size):
    window = make_window()
    assert_selected_size(window, 12)
    view = next(action.menu() for action in window.menuBar().actions()
                if action.text().replace("&", "") == "View")
    submenu = next(action.menu() for action in view.actions()
                   if action.text().replace("&", "") == "Notation Font Size")
    assert set(submenu.actions()) == set(window.notation_font_actions.values())
    window._navigate(lambda: window.player.go_to((1, 0)))
    snapshot = position_snapshot(window)
    fonts = other_fonts(window)
    notation = window.notation.toPlainText()
    original_pgn = window.player.collection.path.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("Changing notation size must not reopen or reload the PGN")

    with monkeypatch.context() as patches:
        patches.setattr(window, "open_file", forbidden)
        patches.setattr(window_module, "index_pgn", forbidden)
        patches.setattr(window_module, "open_pgn", forbidden)
        patches.setattr(window.player.collection, "load_game", forbidden)
        window.notation_font_actions[size].trigger()
    assert_selected_size(window, size)
    assert window.store.get_settings()["notation_font_size"] == size
    assert_same_position(window, snapshot)
    assert other_fonts(window) == fonts
    assert window.notation.toPlainText() == notation
    for text in ("e4", "SECRET inline comment", "d5"):
        cursor = window.notation.document().find(text)
        assert not cursor.isNull()
        assert cursor.charFormat().fontPointSize() == size
    assert window.player.collection.path.read_bytes() == original_pgn
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    window.store.close()

    reopened = make_window()
    assert_selected_size(reopened, size)
    assert reopened.store.get_settings()["notation_font_size"] == size
    assert reopened.notation.document().find("SECRET inline comment").charFormat().fontPointSize() == size


def test_font_change_preserves_running_autoplay_timer_and_variation(make_window):
    window = make_window()
    window._navigate(lambda: window.player.go_to((1,)))
    window.speed.setValue(30)
    window.loop_box.setChecked(True)
    window.toggle_play()
    assert window.autoplay.playing
    timer_id = window._play_timer.timerId()
    interval = window._play_timer.interval()
    snapshot = position_snapshot(window)
    for size in (18, 10, 14):
        window.notation_font_actions[size].trigger()
        assert_selected_size(window, size)
        assert_same_position(window, snapshot)
        assert window.autoplay.playing
        assert window.autoplay.loop
        assert window._play_timer.isActive()
        assert window._play_timer.timerId() == timer_id
        assert window._play_timer.interval() == interval


def test_notation_size_survives_live_theme_changes(make_window):
    window = make_window()
    window._navigate(lambda: window.player.go_to((1, 0)))
    window.notation_font_actions[16].trigger()
    snapshot = position_snapshot(window)
    fonts = other_fonts(window)
    for theme in ("dark", "light"):
        window.toggle_theme()
        assert window.theme == theme
        assert_selected_size(window, 16)
        assert_same_position(window, snapshot)
        assert other_fonts(window) == fonts
        for text in ("e4!", "SECRET inline comment", "d5"):
            cursor = window.notation.document().find(text)
            assert not cursor.isNull()
            assert cursor.charFormat().fontPointSize() == 16
        comment = window.notation.document().find("SECRET inline comment")
        assert comment.charFormat().foreground().color().name() == COLORS[theme]["muted"].lower()
        assert not window.notation.grab().isNull()


def test_font_change_preserves_guess_session_and_keeps_answers_hidden(make_window):
    window = make_window()
    window.guess_color.setCurrentIndex(window.guess_color.findData("both"))
    window.guess_variations.setChecked(True)
    window._toggle_guess(True)
    window._board_move(chess.E2, chess.E4)
    window._board_move(chess.C7, chess.C5)
    assert (window.guess.correct, window.guess.incorrect) == (1, 1)
    assert window.guess.pending_answer == "e5"
    feedback = window.guess_feedback.text()
    snapshot = position_snapshot(window)
    for size in (18, 10, 14):
        window.notation_font_actions[size].trigger()
        assert_selected_size(window, size)
        assert_same_position(window, snapshot)
        assert window.guess.mode == "both"
        assert window.guess_color.currentData() == "both"
        assert window.guess.include_variations
        assert window.guess_variations.isChecked()
        assert window.guess.guesses == 2
        assert (window.guess.correct, window.guess.incorrect) == (1, 1)
        assert window.guess.pending_answer == "e5"
        assert window.guess_feedback.text() == feedback
        assert window.guess_button.isChecked()
        assert window.guess_panel.isVisible()
        assert window.guess_continue.isVisible()
        assert not window.play_button.isEnabled()
        assert not window._opponent_timer.isActive()
        notation = window.notation.toPlainText()
        assert "e4" in notation
        for hidden in ("SECRET", "e5", "Nf3", "d4"):
            assert hidden not in notation


def test_font_change_does_not_restart_pending_guess_opponent_timer(make_window):
    window = make_window()
    window._toggle_guess(True)
    window._board_move(chess.E2, chess.E4)
    assert window._opponent_timer.isActive()
    timer_id = window._opponent_timer.timerId()
    snapshot = position_snapshot(window)
    window.notation_font_actions[18].trigger()
    assert_same_position(window, snapshot)
    assert window.guess.mode == "white"
    assert window.guess.correct == 1
    assert window._opponent_timer.isActive()
    assert window._opponent_timer.timerId() == timer_id
    assert not window.board.input_enabled


def test_font_shortcuts_on_readonly_notation_update_menu_and_clamp(make_window, qtbot, qapp):
    window = make_window()
    activate_offscreen(window, window.notation, qtbot, qapp)
    snapshot = position_snapshot(window)
    ctrl = Qt.KeyboardModifier.ControlModifier
    for key, modifiers, expected in (
        (Qt.Key.Key_Plus, ctrl | Qt.KeyboardModifier.ShiftModifier, 14),
        (Qt.Key.Key_Equal, ctrl, 16),
        (Qt.Key.Key_Minus, ctrl, 14),
        (Qt.Key.Key_0, ctrl, 12),
    ):
        qtbot.keyClick(window.notation, key, modifiers)
        assert_selected_size(window, expected)
        assert window.store.get_settings()["notation_font_size"] == expected
        assert_same_position(window, snapshot)
    for _ in range(8):
        qtbot.keyClick(window.notation, Qt.Key.Key_Plus, ctrl)
    assert_selected_size(window, 18)
    for _ in range(8):
        qtbot.keyClick(window.notation, Qt.Key.Key_Minus, ctrl)
    assert_selected_size(window, 10)
    qtbot.keyClick(window.notation, Qt.Key.Key_0, ctrl)
    assert_selected_size(window, 12)


@pytest.mark.parametrize("entry", ["line_edit", "spin_box", "editable_text"])
def test_font_shortcuts_do_not_apply_in_text_entry(make_window, qtbot, qapp, entry):
    window = make_window()
    window.notation_font_actions[14].trigger()
    if entry == "line_edit":
        widget = window.game_list.search
    elif entry == "spin_box":
        widget = window.speed
    else:
        widget = QTextEdit(window)
        widget.setPlainText("Editable notes")
        window.centralWidget().layout().addWidget(widget)
        widget.show()
    activate_offscreen(window, widget, qtbot, qapp)
    target = qapp.focusWidget()
    for key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus, Qt.Key.Key_0):
        qtbot.keyClick(target, key, Qt.KeyboardModifier.ControlModifier)
        assert_selected_size(window, 14)
        assert window.store.get("notation_font_size") == 14


def test_font_shortcuts_do_not_apply_while_modal_dialog_is_open(make_window, qtbot, qapp):
    window = make_window()
    window.notation_font_actions[14].trigger()
    dialog = QDialog(window)
    dialog.setModal(True)
    layout = QVBoxLayout(dialog)
    button = QPushButton("Close", dialog)
    layout.addWidget(button)
    qtbot.addWidget(dialog)
    dialog.show()
    activate_offscreen(dialog, button, qtbot, qapp)
    assert qapp.activeModalWidget() is dialog
    try:
        for key in (Qt.Key.Key_Plus, Qt.Key.Key_Minus, Qt.Key.Key_0):
            qtbot.keyClick(button, key, Qt.KeyboardModifier.ControlModifier)
            assert_selected_size(window, 14)
    finally:
        dialog.close()
