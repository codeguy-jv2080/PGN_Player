"""Offscreen rendering and UI checks for independently toggled coordinates."""
import os
from pathlib import Path

import chess
import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFontDatabase, QImage

from pgn_player.paths import resolve_paths
from pgn_player.storage import SettingsStore
from pgn_player.ui.board import BoardWidget
from pgn_player.ui.main_window import MainWindow
from pgn_player.ui.themes import COLORS


PGN = '''[Event "Coordinates study"]
[White "Ada"]
[Black "Ben"]
[Result "*"]

1. e4 {SECRET answer explanation} (1. d4 d5 2. c4) e5 2. Nf3 Nc6 *
'''


@pytest.fixture
def coordinate_font(qapp):
    assert qapp.platformName() == "offscreen"
    # Windows' offscreen plugin does not enumerate installed system fonts.
    # Register this installed font only in the test process; never copy it.
    path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
    font_id = QFontDatabase.addApplicationFont(str(path)) if path.is_file() else -1
    yield
    if font_id >= 0:
        QFontDatabase.removeApplicationFont(font_id)


def render(widget):
    image = QImage(widget.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    widget.render(image)
    return image


@pytest.mark.parametrize("orientation", ["white", "black"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_hiding_coordinates_removes_only_exterior_labels(qtbot, coordinate_font, orientation, theme):
    widget = BoardWidget()
    qtbot.addWidget(widget)
    widget.resize(514, 514)
    widget.orientation = orientation
    widget.theme = theme
    position = chess.Board()
    move = chess.Move.from_uci("e2e4")
    position.push(move)
    widget.set_position(position, move)
    widget.input_enabled = True
    widget.selected = chess.G8
    assert widget.coordinates_visible
    box = widget.board_rect()
    squares = [widget.square_rect(square) for square in chess.SQUARES]
    visible = render(widget)
    widget.coordinates_visible = False
    widget.update()
    hidden = render(widget)

    assert widget.board_rect() == box
    assert [widget.square_rect(square) for square in chess.SQUARES] == squares
    assert all(widget.square_at(rect.center()) == square for square, rect in enumerate(squares))
    assert widget.selected == chess.G8
    assert widget.board.fen() == position.fen()
    assert visible.copy(box.toRect()) == hidden.copy(box.toRect())
    labels = (
        QRect(int(box.x()), int(box.bottom()) + 1, int(box.width()), 16),
        QRect(int(box.x()) - 17, int(box.y()), 15, int(box.height())),
    )
    for region in labels:
        before, after = visible.copy(region), hidden.copy(region)
        blank = QImage(region.size(), QImage.Format.Format_ARGB32)
        blank.fill(QColor(COLORS[theme]["background"]))
        assert before != after, "Coordinate labels must actually be rendered before hiding"
        assert after == blank
    widget.coordinates_visible = True
    widget.update()
    assert render(widget) == visible


@pytest.fixture
def make_window(qtbot, tmp_path, qapp):
    assert qapp.platformName() == "offscreen"
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


def close_store(window, qtbot):
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    window.store.close()


def assert_coordinates(window, visible):
    assert window.coordinates_action.text() == "Show board coordinates"
    assert window.coordinates_action.isCheckable()
    assert window.coordinates_action.isChecked() is visible
    assert window.board.coordinates_visible is visible
    assert window.store.get_settings()["board_coordinates_visible"] is visible


def test_coordinate_menu_persists_independently_of_notation_visibility(make_window, qtbot):
    window = make_window()
    assert_coordinates(window, True)
    assert window.notation_panel.isVisible()
    window.coordinates_action.trigger()
    assert_coordinates(window, False)
    assert window.notation_panel.isVisible()
    assert window.notation_button.text() == "Hide notation"
    window._set_notation_visible(False)
    window.coordinates_action.trigger()
    assert_coordinates(window, True)
    assert window.notation_panel.isHidden()
    window.coordinates_action.trigger()
    close_store(window, qtbot)

    hidden = make_window()
    assert_coordinates(hidden, False)
    assert hidden.notation_panel.isHidden()
    hidden._set_notation_visible(True)
    assert_coordinates(hidden, False)
    hidden.coordinates_action.trigger()
    close_store(hidden, qtbot)
    visible = make_window()
    assert_coordinates(visible, True)
    assert visible.notation_panel.isVisible()


def test_coordinate_toggle_preserves_autoplay_position_and_timer(make_window):
    window = make_window()
    window._navigate(lambda: window.player.go_to((1,)))
    window.speed.setValue(30)
    window.toggle_play()
    assert window.autoplay.playing
    timer_id = window._play_timer.timerId()
    collection, game, node = window.player.collection, window.player.game, window.player.node
    board = window.board.board
    position = window.player.board.fen()
    for visible in (False, True):
        window.coordinates_action.trigger()
        assert_coordinates(window, visible)
        assert window.player.collection is collection
        assert window.player.game is game
        assert window.player.node is node
        assert window.player.path == (1,)
        assert window.player.board.fen() == position
        assert window.board.board is board
        assert window.autoplay.playing
        assert window._play_timer.isActive()
        assert window._play_timer.timerId() == timer_id


def test_coordinate_toggle_preserves_selected_piece_guess_input_and_reply_timer(make_window, qtbot):
    window = make_window()
    window.guess_variations.setChecked(True)
    window._toggle_guess(True)
    qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton,
                    pos=window.board.square_rect(chess.E2).center().toPoint())
    assert window.board.selected == chess.E2
    window.coordinates_action.trigger()
    assert_coordinates(window, False)
    assert window.board.selected == chess.E2
    assert window.board.input_enabled
    qtbot.mouseClick(window.board, Qt.MouseButton.LeftButton,
                    pos=window.board.square_rect(chess.E4).center().toPoint())
    assert window.guess.correct == 1
    assert window._opponent_timer.isActive()
    timer_id = window._opponent_timer.timerId()
    position = window.player.board.fen()
    for visible in (True, False):
        window.coordinates_action.trigger()
        assert_coordinates(window, visible)
        assert window.guess.mode == "white"
        assert window.guess.include_variations
        assert window.guess.correct == 1
        assert window.guess.incorrect == 0
        assert window.guess_button.isChecked()
        assert window.guess_panel.isVisible()
        assert window.player.board.fen() == position
        assert window.player.path == (0,)
        assert not window.board.input_enabled
        assert window._opponent_timer.isActive()
        assert window._opponent_timer.timerId() == timer_id
        assert "e5" not in window.notation.toPlainText()
        assert "SECRET" not in window.notation.toPlainText()
