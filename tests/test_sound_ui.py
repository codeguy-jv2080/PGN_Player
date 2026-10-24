"""Offscreen sound routing tests using a silent service, never the speakers."""
import chess
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from pgn_player.paths import resolve_paths
from pgn_player.storage import SettingsStore
from pgn_player.ui.main_window import MainWindow
import pgn_player.ui.main_window as window_module


PGN = '''[Event "Sound study"]

1. e4 (1. d4 d5 2. c4) d5 2. exd5 Qxd5 *

[Event "Second study"]

1. c4 e5 *
'''


class SilentSounds:
    def __init__(self):
        self.enabled = False
        self.played = []
        self.stop_count = 0

    def set_enabled(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.stop()

    def play(self, *, capture=False):
        if self.enabled:
            self.played.append("capture" if capture else "move")

    def stop(self):
        self.stop_count += 1


@pytest.fixture
def make_window(qtbot, tmp_path, qapp, monkeypatch):
    assert qapp.platformName() == "offscreen"
    monkeypatch.setattr(window_module, "MoveSounds", SilentSounds)
    paths = resolve_paths("development", app_dir=tmp_path, data_override=tmp_path / "state")
    windows = []

    def create(pgn=PGN):
        source = tmp_path / f"study-{len(windows)}.pgn"
        source.write_text(pgn, encoding="utf-8")
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


def enable_sound(window):
    assert not window.sound_action.isChecked()
    window.sound_action.trigger()
    assert window.sound_action.isChecked()
    assert window.move_sounds.enabled
    assert window.move_sounds.played == []


def playback_tick(window):
    assert window.autoplay.playing
    window._play_timer.stop()
    window._autoplay_tick()


def opponent_tick(window):
    window._opponent_timer.stop()
    window._opponent_tick()


def test_sound_menu_defaults_off_and_remembers_enabled_state_after_restart(make_window, qtbot):
    window = make_window()
    assert window.sound_action.text() == "Sound"
    assert window.sound_action.isCheckable()
    assert not window.sound_action.isChecked()
    assert not window.move_sounds.enabled
    window._navigate(window.player.next)
    assert window.move_sounds.played == []
    enable_sound(window)
    assert window.store.get("sound_enabled") is True
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    window.store.close()
    reopened = make_window()
    assert reopened.sound_action.isChecked()
    assert reopened.move_sounds.enabled
    assert reopened.move_sounds.played == []  # Opening/restoring never emits a move.


@pytest.mark.parametrize("pgn, before, expected", [
    ('1. e4 *', (), "move"),
    ('1. e4 d5 2. exd5 *', (0, 0), "capture"),
    ('[SetUp "1"]\n[FEN "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1"]\n\n1. exd6 *', (), "capture"),
    ('[SetUp "1"]\n[FEN "7k/P7/8/8/8/8/7p/4K3 w - - 0 1"]\n\n1. a8=Q+ *', (), "move"),
    ('[SetUp "1"]\n[FEN "1r5k/P7/8/8/8/8/8/4K3 w - - 0 1"]\n\n1. axb8=Q+ *', (), "capture"),
    ('[SetUp "1"]\n[FEN "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"]\n\n1. O-O *', (), "move"),
    ('1. -- *', (), None),
], ids=["quiet", "capture", "en-passant", "promotion", "capture-promotion", "castling", "null-move"])
def test_direct_forward_move_uses_pre_move_position_to_classify_sound(make_window, pgn, before, expected):
    window = make_window(pgn)
    enable_sound(window)
    window._navigate(lambda: window.player.go_to(before))
    assert window.move_sounds.played == []
    window._navigate(window.player.next)
    assert window.player.path == before + (0,)
    assert window.move_sounds.played == ([] if expected is None else [expected])
    window._refresh()
    window._navigate(window.player.next)  # Already at the recorded end.
    assert window.move_sounds.played == ([] if expected is None else [expected])


def test_rewinds_jumps_game_changes_loads_and_presentation_changes_are_silent(make_window, qtbot):
    window = make_window()
    enable_sound(window)
    window._navigate(window.player.next)
    assert window.move_sounds.played == ["move"]
    window._navigate(lambda: window.player.go_to((0,)))  # Same node.
    window._navigate(window.player.first)
    window._navigate(window.player.last)
    window._navigate(window.player.next)
    window._navigate(window.player.previous)
    window._navigate(window.player.next_game)
    window._navigate(window.player.previous_game)
    window.notation.move_selected.emit((0, 0))  # Jumping two plies.
    assert window.move_sounds.played == ["move"]
    window.notation.move_selected.emit((0, 0, 0))
    assert window.move_sounds.played == ["move", "capture"]
    window._navigate(lambda: window.player.go_to((1,)))  # Jumping to a sibling line.
    window._navigate(window.player.first)
    window.notation.move_selected.emit((1,))  # One forward variation move.
    assert window.move_sounds.played == ["move", "capture", "move"]
    window.toggle_theme()
    window._set_notation_font_size(16)
    window.flip_board()
    window._set_notation_visible(False)
    window._set_notation_visible(True)
    window.open_file(window.player.collection.path)
    qtbot.waitUntil(lambda: not window._loading and window._worker is None, timeout=10000)
    assert window.move_sounds.played == ["move", "capture", "move"]


def test_autoplay_sounds_once_per_move_and_mute_keeps_timer_and_loop_running(make_window):
    window = make_window('1. e4 d5 2. exd5 Qxd5 *')
    enable_sound(window)
    window.speed.setValue(30)
    window.continue_box.setChecked(False)
    window.loop_box.setChecked(True)
    window.toggle_play()
    for _ in range(4):
        playback_tick(window)
    assert window.move_sounds.played == ["move", "move", "capture", "capture"]
    playback_tick(window)  # Loop boundary: reset to the starting position.
    assert window.player.path == ()
    assert len(window.move_sounds.played) == 4
    playback_tick(window)
    assert window.move_sounds.played[-1] == "move"
    assert len(window.move_sounds.played) == 5
    timer_id = window._play_timer.timerId()
    node, board = window.player.node, window.board.board
    window.sound_action.trigger()
    assert not window.move_sounds.enabled
    assert window.autoplay.playing and window.autoplay.loop
    assert window._play_timer.timerId() == timer_id
    assert window.player.node is node and window.board.board is board
    playback_tick(window)
    assert window.player.path == (0, 0)
    assert len(window.move_sounds.played) == 5
    timer_id = window._play_timer.timerId()
    window.sound_action.trigger()
    assert window._play_timer.timerId() == timer_id
    assert len(window.move_sounds.played) == 5
    playback_tick(window)
    assert window.move_sounds.played[-1] == "capture"
    assert len(window.move_sounds.played) == 6


def test_autoplay_variation_capture_uses_that_branch_parent_board(make_window):
    window = make_window('1. e4 d5 2. e5 (2. exd5 Qxd5) d4 *')
    enable_sound(window)
    window.autoplay.include_variations = True
    window.speed.setValue(30)
    window.toggle_play()
    for _ in range(4):
        playback_tick(window)
    assert window.player.path == (0, 0, 0, 0)
    assert window.move_sounds.played == ["move"] * 4
    playback_tick(window)
    assert window.player.path == (0, 0, 1)
    assert window.move_sounds.played == ["move"] * 4 + ["capture"]
    playback_tick(window)
    assert window.move_sounds.played == ["move"] * 4 + ["capture", "capture"]


def test_autoplay_next_game_and_collection_wrap_do_not_emit_extra_sounds(make_window):
    window = make_window('[Event "First"]\n\n1. e4 *\n\n[Event "Second"]\n\n1. d4 *\n')
    enable_sound(window)
    window.loop_box.setChecked(True)
    window.speed.setValue(30)
    window.toggle_play()
    for game, path, count in ((0, (0,), 1), (1, (), 1), (1, (0,), 2), (0, (), 2), (0, (0,), 3)):
        playback_tick(window)
        assert (window.player.game_index, window.player.path) == (game, path)
        assert window.move_sounds.played == ["move"] * count


def test_optional_sound_error_does_not_interrupt_navigation_or_autoplay(make_window, monkeypatch):
    window = make_window()
    enable_sound(window)

    def fail(*, capture=False):
        raise RuntimeError("Synthetic audio failure")

    monkeypatch.setattr(window.move_sounds, "play", fail)
    window._navigate(window.player.next)
    assert window.player.path == (0,)
    window.speed.setValue(30)
    window.toggle_play()
    playback_tick(window)
    assert window.player.path == (0, 0)
    assert window.autoplay.playing
    assert window._play_timer.isActive()
    assert window._status_error == ""


def test_guess_wrong_illegal_and_noop_are_silent_but_applied_moves_sound_once(make_window):
    window = make_window()
    enable_sound(window)
    window._toggle_guess(True)
    window._continue_guess()  # No pending answer to apply.
    window._board_move(chess.E2, chess.E5)  # Illegal.
    assert window.move_sounds.played == []
    window._board_move(chess.E2, chess.E4)
    assert window.move_sounds.played == ["move"]
    timer_id = window._opponent_timer.timerId()
    window.sound_action.trigger()
    window.sound_action.trigger()
    assert window.guess.mode == "white" and window.guess.correct == 1
    assert window._opponent_timer.timerId() == timer_id
    assert window.move_sounds.played == ["move"]
    opponent_tick(window)
    opponent_tick(window)  # Now the user is to move; no second reply.
    assert window.move_sounds.played == ["move", "move"]
    window._board_move(chess.G1, chess.F3)  # Legal but not the recorded capture.
    assert window.guess.incorrect == 1
    assert window.move_sounds.played == ["move", "move"]
    window._continue_guess()
    window._continue_guess()  # The answer has already been applied.
    assert window.move_sounds.played == ["move", "move", "capture"]
    opponent_tick(window)
    opponent_tick(window)
    assert window.move_sounds.played == ["move", "move", "capture", "capture"]
    assert window.player.at_end


@pytest.mark.parametrize("control", ["buttons", "arrows", "native_keypad", "wheel"])
def test_rapid_forward_inputs_emit_once_per_move_and_rewinds_stay_silent(make_window, qtbot, qapp, control):
    window = make_window()
    enable_sound(window)
    qapp.setActiveWindow(window)
    window.notation.setFocus()
    qtbot.waitUntil(lambda: window.isActiveWindow() and qapp.focusWidget() is window.notation)

    def step(forward):
        if control == "buttons":
            qtbot.mouseClick(window.next_button if forward else window.previous_button, Qt.MouseButton.LeftButton)
        elif control == "arrows":
            qtbot.keyClick(window.notation, Qt.Key.Key_Right if forward else Qt.Key.Key_Left)
        elif control == "native_keypad":
            # Supply Windows scan/virtual codes in synthetic events only; the
            # test never touches a physical keyboard or changes system Num Lock.
            key, scan, virtual = (Qt.Key.Key_6, 0x4D, 0x66) if forward else (Qt.Key.Key_4, 0x4B, 0x64)
            event = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.KeypadModifier,
                              scan, virtual, 0, "", False)
            QApplication.sendEvent(window.notation, event)
        else:
            point = window.board.board_rect().center()
            event = QWheelEvent(point, QPointF(window.board.mapToGlobal(point.toPoint())),
                                QPoint(), QPoint(0, -120 if forward else 120),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(window.board, event)

    expected = ["move", "move", "capture", "capture"]
    # A burst of real UI input paths, with no wait for audio between moves.
    for count in range(1, 5):
        step(True)
        assert window.player.path == (0,) * count
        assert window.move_sounds.played == expected[:count]
    for _ in range(3):
        step(True)
    assert window.move_sounds.played == expected
    assert window.player.game_index == 0
    for _ in range(6):
        step(False)
        assert window.move_sounds.played == expected
    assert window.player.path == ()

    step(True)
    step(True)
    expected += ["move", "move"]
    for _ in range(12):
        step(True)
        expected.append("capture")
        assert window.player.path == (0, 0, 0)
        assert window.move_sounds.played == expected
        step(False)
        assert window.player.path == (0, 0)
        assert window.move_sounds.played == expected
    assert window.player.game_index == 0
    assert not window.autoplay.playing
