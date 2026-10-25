"""Chess960 board input and integrated navigation, entirely offscreen and silent."""
import chess
import chess.pgn
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from pgn_player.paths import resolve_paths
from pgn_player.storage import SettingsStore
from pgn_player.ui.board import BoardWidget
from pgn_player.ui.main_window import MainWindow
import pgn_player.ui.main_window as window_module


# Synthetic legal positions; the other king is remote from the castling path.
CASTLES = [
    ("4K2R", "H", "e1h1", "O-O"),
    ("RK6", "A", "b1a1", "O-O-O"),
    ("6KR", "H", "g1h1", "O-O"),       # Stationary king.
    ("R1K5", "A", "c1a1", "O-O-O"),
    ("4KR2", "F", "e1f1", "O-O"),     # Stationary rook.
    ("3RK3", "D", "e1d1", "O-O-O"),
    ("5KR1", "G", "f1g1", "O-O"),     # King/rook exchange squares.
    ("2RK4", "C", "d1c1", "O-O-O"),
    ("1K5R", "H", "b1h1", "O-O"),     # Overlapping movement paths.
    ("R5K1", "A", "g1a1", "O-O-O"),
]


def castle_position(case, color=chess.WHITE):
    rank, rights, uci, san = case
    board = chess.Board(f"4k3/8/8/8/8/8/8/{rank} w {rights} - 0 1", chess960=True)
    move = chess.Move.from_uci(uci)
    if color == chess.BLACK:
        board = board.mirror()
        move = chess.Move(chess.square_mirror(move.from_square), chess.square_mirror(move.to_square))
    assert board.is_valid()
    assert move in board.legal_moves
    assert board.san(move) == san
    return board, move


def enter_move(widget, move, method, qtbot):
    origin = widget.square_rect(move.from_square).center().toPoint()
    destination = widget.square_rect(move.to_square).center().toPoint()
    if method == "click":
        qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=origin)
        qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=destination)
    else:
        qtbot.mousePress(widget, Qt.MouseButton.LeftButton, pos=origin)
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(destination),
                            QPointF(widget.mapToGlobal(destination)), Qt.MouseButton.NoButton,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(widget, event)
        assert widget._drag_point is not None
        qtbot.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=destination)


@pytest.mark.parametrize("case", CASTLES, ids=[case[2] for case in CASTLES])
@pytest.mark.parametrize("color", [chess.WHITE, chess.BLACK], ids=["white", "black"])
@pytest.mark.parametrize("orientation", ["white", "black"])
@pytest.mark.parametrize("method", ["click", "drag"])
def test_legal_castling_input_emits_king_to_rook(qtbot, qapp, case, color, orientation, method):
    assert qapp.platformName() == "offscreen"
    position, move = castle_position(case, color)
    widget = BoardWidget()
    qtbot.addWidget(widget)
    widget.resize(500, 500)
    widget.orientation = orientation
    widget.set_position(position)
    widget.input_enabled = True
    requested = []
    widget.move_requested.connect(lambda origin, destination: requested.append((origin, destination)))
    enter_move(widget, move, method, qtbot)
    assert requested == [(move.from_square, move.to_square)]
    assert widget.selected is None
    assert widget._drag_point is None
    assert widget.board.fen() == position.fen()  # Input never changes the board itself.


@pytest.mark.parametrize("method", ["click", "drag"])
@pytest.mark.parametrize("fen, uci", [
    ("4k3/8/8/8/8/8/8/4K2R w - - 0 1", "e1h1"),  # No rights.
    ("4k3/8/8/8/8/8/8/4KB1R w H - 0 1", "e1h1"), # Obstruction.
    ("k3r3/8/8/8/8/8/8/4K2R w H - 0 1", "e1h1"), # In check.
    ("k4r2/8/8/8/8/8/8/4K2R w H - 0 1", "e1h1"), # Through check.
    ("k5r1/8/8/8/8/8/8/4K2R w H - 0 1", "e1h1"), # Destination attacked.
    ("4k3/8/8/8/8/8/8/R3K2R w HA - 0 1", "a1h1"), # Rook to own rook.
    ("4k3/8/8/8/8/8/8/4KN2 w - - 0 1", "e1f1"), # King to own knight.
])
def test_friendly_targets_remain_blocked_unless_castling_is_legal(qtbot, fen, uci, method):
    widget = BoardWidget()
    qtbot.addWidget(widget)
    widget.resize(500, 500)
    position = chess.Board(fen, chess960=True)
    move = chess.Move.from_uci(uci)
    assert move not in position.legal_moves
    widget.set_position(position)
    widget.input_enabled = True
    requested = []
    widget.move_requested.connect(lambda *args: requested.append(args))
    enter_move(widget, move, method, qtbot)
    assert requested == []
    assert widget.board.fen() == position.fen()
    if method == "click":
        assert widget.selected == move.to_square  # Ordinary friendly-piece selection still works.


class SilentSounds:
    def __init__(self):
        self.enabled = False
        self.played = []

    def set_enabled(self, enabled):
        self.enabled = enabled

    def stop(self):
        pass

    def play(self, *, capture=False):
        if self.enabled:
            self.played.append("capture" if capture else "move")


@pytest.fixture
def make_window(qtbot, qapp, tmp_path, monkeypatch):
    assert qapp.platformName() == "offscreen"
    monkeypatch.setattr(window_module, "MoveSounds", SilentSounds)
    paths = resolve_paths("development", app_dir=tmp_path, data_override=tmp_path / "state")
    source = tmp_path / "chess960-study.pgn"
    windows = []

    def create(games=None):
        if games is not None:
            source.write_text("\n\n".join(str(game) for game in games) + "\n", encoding="utf-8")
        store = SettingsStore(paths.db_path)
        window = MainWindow(store, paths)
        windows.append((window, store))
        qtbot.addWidget(window)
        window.show()
        window.open_file(source, restore=games is None)
        qtbot.waitUntil(lambda: not window._loading and window._worker is None, timeout=10000)
        assert window.player.collection is not None, window._status_error
        qapp.setActiveWindow(window)
        window.notation.setFocus()
        window._set_sound_enabled(True)
        return window

    yield create
    for window, store in reversed(windows):
        if store._connection is not None:
            window.close()
            qtbot.waitUntil(lambda: window._worker is None and not window.isVisible(), timeout=5000)
            store.close()


def castle_game(case=CASTLES[0], color=chess.WHITE, variation=False):
    board, move = castle_position(case, color)
    game = chess.pgn.Game.from_board(board)
    if variation:
        quiet = next(candidate for candidate in board.legal_moves if not board.is_castling(candidate))
        game.add_variation(quiet, comment="MAINLINE SECRET")
    child = game.add_variation(move, comment="CASTLING SECRET")
    child.nags.add(chess.pgn.NAG_GOOD_MOVE)
    return game


@pytest.mark.parametrize("case", [CASTLES[0], CASTLES[1]], ids=["kingside", "queenside"])
@pytest.mark.parametrize("mode,color", [("white", chess.WHITE), ("black", chess.BLACK),
                                        ("both", chess.WHITE), ("both", chess.BLACK)])
@pytest.mark.parametrize("method", ["click", "drag"])
def test_training_scores_castling_and_uses_move_sound(make_window, qtbot, case, mode, color, method):
    game = castle_game(case, color)
    window = make_window([game])
    window.guess_color.setCurrentIndex(window.guess_color.findData(mode))
    window._toggle_guess(True)
    assert window.board.input_enabled
    assert "CASTLING SECRET" not in window.notation.toPlainText()
    assert case[3] not in window.notation.toPlainText()
    assert not window.play_button.isEnabled()
    enter_move(window.board, game.variations[0].move, method, qtbot)
    assert window.guess.correct == 1 and window.guess.incorrect == 0
    assert window.player.path == (0,)
    assert window.player.board.fen() == game.end().board().fen()
    assert "End of recorded line" in window.guess_feedback.text()
    assert case[3] in window.notation.toPlainText()
    assert "CASTLING SECRET" not in window.notation.toPlainText()
    assert window.move_sounds.played == ["move"]
    assert window.guess.mode == mode
    window._navigate(window.player.previous)
    assert window.player.board.fen() == game.board().fen()
    assert window.board.input_enabled
    assert window.move_sounds.played == ["move"]  # Backward moves remain silent.


@pytest.mark.parametrize("accept", [False, True])
def test_training_variation_castling_and_incorrect_guess(make_window, qtbot, accept):
    game = castle_game(variation=True)
    window = make_window([game])
    window.guess_variations.setChecked(accept)
    window._toggle_guess(True)
    castle = game.variations[1]
    enter_move(window.board, castle.move, "click", qtbot)
    assert (window.guess.correct, window.guess.incorrect) == ((1, 0) if accept else (0, 1))
    assert window.player.path == ((1,) if accept else ())
    assert window.move_sounds.played == (["move"] if accept else [])
    assert "SECRET" not in window.notation.toPlainText()
    if not accept:
        assert window.guess.pending_answer == game.variations[0].san()
        window._continue_guess()
        assert window.player.path == (0,)
        assert window.player.board.fen() == game.variations[0].board().fen()


@pytest.mark.parametrize("chess960", [False, True])
@pytest.mark.parametrize("san", ["O-O", "O-O-O", "Kd2"])
def test_standard_castling_and_normal_king_moves_are_unchanged(make_window, qtbot, chess960, san):
    board = chess.Board("4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1", chess960=chess960)
    game = chess.pgn.Game.from_board(board)
    game.add_variation(board.parse_san(san))
    window = make_window([game])
    window._toggle_guess(True)
    enter_move(window.board, game.variations[0].move, "click", qtbot)
    assert window.guess.correct == 1
    assert window.player.board.fen() == game.end().board().fen()
    assert window.move_sounds.played == ["move"]


def wheel(widget, amount):
    point = widget.board_rect().center()
    event = QWheelEvent(point, QPointF(widget.mapToGlobal(point.toPoint())), QPoint(),
                        QPoint(0, amount), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    assert event.isAccepted()


@pytest.mark.parametrize("num_lock", [False, True])
def test_wheel_keypad_keyboard_and_resume_across_castling(make_window, qtbot, num_lock):
    first = castle_game(CASTLES[2])  # Stationary king.
    second = castle_game(CASTLES[3], chess.BLACK)  # Black stationary king.
    window = make_window([first, second])

    def keypad(number, off_key):
        key = getattr(Qt.Key, f"Key_{number}") if num_lock else off_key
        QApplication.sendEvent(window.notation, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.KeypadModifier))

    wheel(window.board, -120)
    assert window.player.board.fen() == first.end().board().fen()
    wheel(window.board, 120)
    assert window.player.board.fen() == first.board().fen()
    keypad(6, Qt.Key.Key_Right)
    assert window.player.board.fen() == first.end().board().fen()
    keypad(4, Qt.Key.Key_Left)
    assert window.player.board.fen() == first.board().fen()
    keypad(8, Qt.Key.Key_Up)
    assert window.player.game_index == 1 and window.player.board.fen() == second.board().fen()
    keypad(9, Qt.Key.Key_PageUp)
    assert window.player.board.fen() == second.end().board().fen()
    keypad(7, Qt.Key.Key_Home)
    assert window.player.board.fen() == second.board().fen()
    keypad(2, Qt.Key.Key_Down)
    assert window.player.game_index == 0 and window.player.board.fen() == first.board().fen()
    qtbot.keyClick(window.notation, Qt.Key.Key_Right)
    assert window.player.board.fen() == first.end().board().fen()
    qtbot.keyClick(window.notation, Qt.Key.Key_Left)
    assert window.player.board.fen() == first.board().fen()
    window._navigate(window.player.next_game)
    window._navigate(window.player.last)
    window.flip_board()
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    window.store.close()
    reopened = make_window()
    assert reopened.player.game_index == 1
    assert reopened.player.path == (0,)
    assert reopened.player.board.chess960
    assert reopened.player.board.fen() == second.end().board().fen()
    assert reopened.board.orientation == "black"
    reopened._navigate(reopened.player.previous)
    assert reopened.player.board.fen() == second.board().fen()


def render(widget):
    image = QImage(widget.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    widget.render(image)
    return image


@pytest.mark.parametrize("orientation", ["white", "black"])
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("case", [CASTLES[2], CASTLES[3], CASTLES[6], CASTLES[7]])
def test_castling_legal_indicators_positions_and_highlights(qtbot, orientation, theme, case):
    board, move = castle_position(case)
    widget = BoardWidget()
    qtbot.addWidget(widget)
    widget.resize(514, 514)
    widget.orientation = orientation
    widget.theme = theme
    widget.set_position(board)
    widget.selected = move.from_square
    plain = render(widget)
    widget.input_enabled = True
    indicated = render(widget)
    for square in chess.SQUARES:
        cell = widget.square_rect(square)
        assert widget.square_at(cell.center()) == square
        center = cell.center().toPoint()
        if square == move.to_square:
            assert plain.pixelColor(center) != indicated.pixelColor(center)
        elif square == chess.A4:
            assert plain.pixelColor(center) == indicated.pixelColor(center)
    board.push(move)
    widget.set_position(board, move)
    highlighted = render(widget)
    assert widget.board.chess960 and widget.board.piece_map() == board.piece_map()
    assert widget.board.piece_at(chess.G1 if case[3] == "O-O" else chess.C1) == chess.Piece(chess.KING, chess.WHITE)
    assert widget.board.piece_at(chess.F1 if case[3] == "O-O" else chess.D1) == chess.Piece(chess.ROOK, chess.WHITE)
    widget.set_position(board)
    unhighlighted = render(widget)
    for square in chess.SQUARES:
        # A corner inside the square, clear of pieces and coordinate labels.
        point = (widget.square_rect(square).topLeft() + QPointF(2, 2)).toPoint()
        changed = highlighted.pixelColor(point) != unhighlighted.pixelColor(point)
        assert changed == (square in (move.from_square, move.to_square))


def test_autoplay_castling_sounds_and_opponent_replies(make_window, qtbot):
    position = chess.Board("r3k2r/5p2/8/8/8/8/8/R3K2R w HAha - 0 1", chess960=True)
    game = chess.pgn.Game.from_board(position)
    node = game
    for san in ("O-O", "O-O-O", "Rxf7"):
        move = position.parse_san(san)
        node = node.add_variation(move)
        position.push(move)
    window = make_window([game])
    window.speed.setValue(30)
    window.toggle_play()
    for child in game.mainline():
        window._play_timer.stop()
        window._autoplay_tick()
        assert window.player.board.fen() == child.board().fen()
    window._play_timer.stop()
    window._autoplay_tick()
    assert not window.autoplay.playing
    assert window.move_sounds.played == ["move", "move", "capture"]
    window._navigate(window.player.first)
    window.move_sounds.played.clear()
    window._toggle_guess(True)
    enter_move(window.board, game.variations[0].move, "click", qtbot)
    assert window.guess.correct == 1
    assert window._opponent_timer.isActive()
    window._opponent_timer.stop()
    window._opponent_tick()
    assert window.player.path == (0, 0)
    assert window.player.board.fen() == game.variations[0].variations[0].board().fen()
    assert window.move_sounds.played == ["move", "move"]
    assert window.board.input_enabled
