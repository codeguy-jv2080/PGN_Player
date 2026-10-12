"""Synthetic offscreen wheel/keypad events; no physical input or Num Lock changes."""
import sys

import chess
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QTextEdit, QVBoxLayout

from pgn_player.paths import resolve_paths
from pgn_player.storage import SettingsStore
from pgn_player.ui.board import BoardWidget
from pgn_player.ui.main_window import MainWindow


PGN = '''[Event "First"]

1. e4 {SECRET hint} (1. d4 d5 2. c4) e5 2. Nf3 Nc6 *

[Event "Second"]

1. d4 d5 2. c4 e6 *

[Event "Empty game"]

*
'''
KEYPAD = Qt.KeyboardModifier.KeypadModifier
NONE = Qt.KeyboardModifier.NoModifier
# Number form, Num Lock-off form, scan code, virtual key off, expected position.
KEYS = (
    (Qt.Key.Key_8, Qt.Key.Key_Up, 0x48, 0x26, (2, ())),
    (Qt.Key.Key_2, Qt.Key.Key_Down, 0x50, 0x28, (0, ())),
    (Qt.Key.Key_4, Qt.Key.Key_Left, 0x4B, 0x25, (1, (0,))),
    (Qt.Key.Key_6, Qt.Key.Key_Right, 0x4D, 0x27, (1, (0, 0, 0))),
    (Qt.Key.Key_7, Qt.Key.Key_Home, 0x47, 0x24, (1, ())),
    (Qt.Key.Key_9, Qt.Key.Key_PageUp, 0x49, 0x21, (1, (0, 0, 0, 0))),
)


def key_event(target, key, modifiers=NONE, scan=0, virtual=0, repeat=False):
    event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers, scan, virtual, 0, "", repeat)
    QApplication.sendEvent(target, event)
    return event


def wheel_event(target, amount, *, position=None, horizontal=0, pixels=0, buttons=Qt.MouseButton.NoButton):
    local = QPointF(position if position is not None else target.rect().center())
    event = QWheelEvent(local, QPointF(target.mapToGlobal(local.toPoint())),
                        QPoint(0, pixels), QPoint(horizontal, amount),
                        buttons, NONE, Qt.ScrollPhase.NoScrollPhase, False)
    event.ignore()
    QApplication.sendEvent(target, event)
    return event


@pytest.fixture
def make_window(qtbot, tmp_path, qapp):
    assert qapp.platformName() == "offscreen"
    paths = resolve_paths("development", app_dir=tmp_path, data_override=tmp_path / "state")
    source = tmp_path / "inputs.pgn"
    source.write_text(PGN, encoding="utf-8")
    windows = []

    def create(load=True):
        store = SettingsStore(paths.db_path)
        window = MainWindow(store, paths)
        windows.append((window, store))
        qtbot.addWidget(window)
        window.show()
        if load:
            window.open_file(source, restore=False)
            qtbot.waitUntil(lambda: not window._loading and window._worker is None, timeout=10000)
            assert window.player.collection is not None, window._status_error
        qapp.setActiveWindow(window)
        window.notation.setFocus()
        qtbot.waitUntil(lambda: window.isActiveWindow() and qapp.focusWidget() is window.notation)
        return window

    yield create
    for window, store in reversed(windows):
        window.close()
        qtbot.waitUntil(lambda: window._worker is None, timeout=5000)
        qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
        store.close()


def middle_position(window):
    window._navigate(lambda: window.player.select_game(1))
    window._navigate(lambda: window.player.go_to((0, 0)))


def position(window):
    return window.player.game_index, window.player.path


@pytest.mark.parametrize("form", ["num_lock_on", "num_lock_off", "native_fallback"])
def test_all_six_keypad_controls_in_numeric_and_navigation_forms(make_window, form):
    if form == "native_fallback" and sys.platform != "win32":
        pytest.skip("Raw Windows scan-code fallback is Windows-specific")
    window = make_window()
    for number, navigation, scan, virtual_off, expected in KEYS:
        middle_position(window)
        numeric = form == "num_lock_on"
        key = number if numeric else navigation
        virtual = 0x60 + number - Qt.Key.Key_0 if numeric else virtual_off
        key_event(window.notation, key, NONE if form == "native_fallback" else KEYPAD, scan, virtual)
        assert position(window) == expected


def test_number_row_dedicated_navigation_modifiers_and_repeats_keep_existing_behavior(make_window):
    window = make_window()
    middle_position(window)
    unchanged = position(window)
    for number, _, _, _, _ in KEYS:
        key_event(window.notation, number, NONE, number - Qt.Key.Key_0 + 1, number)
        assert position(window) == unchanged
    for key, scan in ((Qt.Key.Key_Up, 0xE048), (Qt.Key.Key_Down, 0xE050), (Qt.Key.Key_PageUp, 0xE049)):
        key_event(window.notation, key, NONE, scan)
        assert position(window) == unchanged
    for modifier in (Qt.KeyboardModifier.AltModifier, Qt.KeyboardModifier.MetaModifier,
                     Qt.KeyboardModifier.ShiftModifier):
        key_event(window.notation, Qt.Key.Key_6, KEYPAD | modifier, 0x4D, 0x66)
        assert position(window) == unchanged
    key_event(window.notation, Qt.Key.Key_6, KEYPAD, 0x4D, 0x66, repeat=True)
    assert position(window) == unchanged
    for key, scan, path in ((Qt.Key.Key_Left, 0xE04B, (0,)),
                            (Qt.Key.Key_Right, 0xE04D, (0, 0, 0)),
                            (Qt.Key.Key_Home, 0xE047, ()),
                            (Qt.Key.Key_End, 0xE04F, (0, 0, 0, 0))):
        middle_position(window)
        key_event(window.notation, key, NONE, scan)
        assert position(window) == (1, path)
    middle_position(window)
    key_event(window.notation, Qt.Key.Key_Right, KEYPAD | Qt.KeyboardModifier.ControlModifier, 0x4D, 0x27)
    assert position(window) == (2, ())  # Existing Ctrl+Right takes priority.


def test_keypad_boundaries_do_not_wrap_and_last_follows_selected_variation(make_window):
    window = make_window()
    for key in (Qt.Key.Key_2, Qt.Key.Key_4, Qt.Key.Key_7):
        key_event(window.notation, key, KEYPAD)
        assert position(window) == (0, ())
    window._navigate(window.player.last)
    for key in (Qt.Key.Key_6, Qt.Key.Key_9):
        key_event(window.notation, key, KEYPAD)
        assert position(window) == (0, (0, 0, 0, 0))

    window._navigate(lambda: window.player.go_to((1, 0)))
    key_event(window.notation, Qt.Key.Key_9, KEYPAD)
    assert position(window) == (0, (1, 0, 0))
    assert window.player.node.san() == "c4"
    key_event(window.notation, Qt.Key.Key_6, KEYPAD)
    assert position(window) == (0, (1, 0, 0))

    window._navigate(lambda: window.player.select_game(2))
    assert window.player.game is not None and window.player.at_end
    for key in (Qt.Key.Key_8, Qt.Key.Key_4, Qt.Key.Key_6, Qt.Key.Key_7, Qt.Key.Key_9):
        key_event(window.notation, key, KEYPAD)
        assert position(window) == (2, ())
    # An empty game still permits explicit navigation to another game.
    key_event(window.notation, Qt.Key.Key_2, KEYPAD)
    assert position(window) == (1, ())

    unloaded = make_window(load=False)
    for number, _, _, _, _ in KEYS:
        key_event(unloaded.notation, number, KEYPAD)
        assert position(unloaded) == (-1, ())
        assert unloaded.player.game is None
        assert unloaded.player.collection is None


@pytest.mark.parametrize("field", ["search", "speed", "editable"])
def test_keypad_navigation_does_not_intercept_text_entry(make_window, qtbot, qapp, field):
    window = make_window()
    middle_position(window)
    if field == "search":
        widget = window.game_list.search
    elif field == "speed":
        widget = window.speed
    else:
        widget = QTextEdit(window)
        window.centralWidget().layout().addWidget(widget)
        widget.show()
    widget.setFocus()
    qtbot.waitUntil(lambda: qapp.focusWidget() is widget or widget.isAncestorOf(qapp.focusWidget()))
    for key, scan in ((Qt.Key.Key_6, 0x4D), (Qt.Key.Key_Up, 0x48), (Qt.Key.Key_PageUp, 0x49)):
        key_event(qapp.focusWidget(), key, KEYPAD, scan)
        assert position(window) == (1, (0, 0))


def test_keypad_navigation_ignores_inactive_and_modal_windows(make_window, qtbot, qapp):
    window = make_window()
    middle_position(window)
    qapp.setActiveWindow(None)
    key_event(window.notation, Qt.Key.Key_6, KEYPAD, 0x4D, 0x66)
    assert position(window) == (1, (0, 0))
    dialog = QDialog(window)
    dialog.setModal(True)
    layout = QVBoxLayout(dialog)
    button = QPushButton("Close", dialog)
    layout.addWidget(button)
    qtbot.addWidget(dialog)
    dialog.show()
    qapp.setActiveWindow(dialog)
    button.setFocus()
    qtbot.waitUntil(lambda: qapp.activeModalWidget() is dialog)
    try:
        key_event(button, Qt.Key.Key_8, KEYPAD, 0x48, 0x68)
        assert position(window) == (1, (0, 0))
    finally:
        dialog.close()


def test_board_wheel_notches_partial_motion_reversal_and_leave(qtbot, qapp):
    assert qapp.platformName() == "offscreen"
    board = BoardWidget()
    qtbot.addWidget(board)
    board.resize(500, 500)
    steps = []
    board.step_requested.connect(steps.append)
    for amount in (120, -120, 240, -240):
        assert wheel_event(board, amount).isAccepted()
    assert steps == [-1, 1, -1, -1, 1, 1]
    steps.clear()
    assert wheel_event(board, 60).isAccepted()
    assert steps == []
    wheel_event(board, 60)
    assert steps == [-1]
    wheel_event(board, -40)
    wheel_event(board, 80)  # A direction change must discard the prior partial notch.
    assert steps == [-1]
    wheel_event(board, 40)
    assert steps == [-1, -1]
    wheel_event(board, 60)
    QApplication.sendEvent(board, QEvent(QEvent.Type.Leave))
    wheel_event(board, 60)
    assert steps == [-1, -1]
    wheel_event(board, 60)
    assert steps == [-1, -1, -1]
    for event in (wheel_event(board, 0, horizontal=120), wheel_event(board, 0, pixels=-30),
                  wheel_event(board, -120, position=QPointF(1, 1))):
        assert not event.isAccepted()
    assert steps == [-1, -1, -1]
    wheel_event(board, 60)
    board.selected = chess.E2
    assert not wheel_event(board, -120, buttons=Qt.MouseButton.LeftButton).isAccepted()
    assert board.selected == chess.E2
    wheel_event(board, 60)
    assert steps == [-1, -1, -1]  # A drag also clears leftover wheel motion.
    wheel_event(board, 60)
    assert steps == [-1, -1, -1, -1]


def test_board_wheel_navigation_stays_within_game_and_other_panes_scroll(make_window, qapp):
    window = make_window()
    wheel_event(window.board, 240)
    assert position(window) == (0, ())
    wheel_event(window.board, -240)
    assert position(window) == (0, (0, 0))
    wheel_event(window.board, 120)
    assert position(window) == (0, (0,))
    wheel_event(window.board, -1200)
    assert position(window) == (0, (0, 0, 0, 0))
    window.notation.setPlainText("\n".join(f"Scrollable line {index}" for index in range(150)))
    qapp.processEvents()
    scrollbar = window.notation.verticalScrollBar()
    assert scrollbar.maximum() > 0
    scrollbar.setValue(scrollbar.maximum() // 2)
    before = scrollbar.value()
    wheel_event(window.notation.viewport(), -120)
    assert scrollbar.value() > before
    assert position(window) == (0, (0, 0, 0, 0))
    window._navigate(lambda: window.player.select_game(2))
    for amount in (120, -120, -240):
        wheel_event(window.board, amount)
        assert position(window) == (2, ())
    unloaded = make_window(load=False)
    wheel_event(unloaded.board, -240)
    assert unloaded.player.game is None
    assert unloaded.player.path == ()


def test_new_inputs_pause_autoplay_and_keep_guess_mode_active(make_window):
    window = make_window()
    window.speed.setValue(30)
    window.toggle_play()
    wheel_event(window.board, -120)
    assert window.player.path == (0,)
    assert not window.autoplay.playing
    assert not window._play_timer.isActive()
    window.toggle_play()
    window._play_timer.stop()
    window._autoplay_tick()
    assert window.player.path == (0, 0)
    assert window.autoplay.playing
    key_event(window.notation, Qt.Key.Key_7, KEYPAD, 0x47, 0x67)
    assert window.player.path == ()
    assert not window.autoplay.playing
    window.guess_variations.setChecked(True)
    window._toggle_guess(True)
    window._board_move(chess.E2, chess.E4)
    assert window._opponent_timer.isActive()
    wheel_event(window.board, 120)
    assert window.guess.mode == "white"
    assert window.guess.correct == 1
    assert window.player.path == ()
    assert not window._opponent_timer.isActive()
    assert window.board.input_enabled
    wheel_event(window.board, -120)
    assert window._opponent_timer.isActive()
    window._opponent_timer.stop()
    window._opponent_tick()
    assert window.player.path == (0, 0)
    assert window.guess.waiting_for_guess
    key_event(window.notation, Qt.Key.Key_8, KEYPAD, 0x48, 0x68)
    assert position(window) == (1, ())
    assert window.guess.mode == "white"
    assert window.guess.correct == 1
    assert window.guess.include_variations
    assert window.guess_panel.isVisible()
    assert window.board.input_enabled
    assert "d4" not in window.notation.toPlainText()
