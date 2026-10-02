"""Verify appearance through offscreen pixels without touching user settings."""

import chess
import pytest
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QPushButton

from pgn_player.paths import resolve_paths
from pgn_player.storage import SettingsStore
from pgn_player.ui.main_window import MainWindow


@pytest.fixture
def themed_window(qapp, qtbot, tmp_path):
    assert qapp.platformName() == "offscreen"
    previous_palette = QPalette(qapp.palette())
    previous_font = QFont(qapp.font())
    previous_style = qapp.styleSheet()
    paths = resolve_paths("development", app_dir=tmp_path, data_override=tmp_path / "state")
    store = SettingsStore(paths.db_path)
    window = None
    try:
        window = MainWindow(store, paths)
        qtbot.addWidget(window)
        window.show()
        qapp.processEvents()
        yield window
    finally:
        if window is not None:
            window.close()
        store.close()
        qapp.setStyleSheet(previous_style)
        qapp.setPalette(previous_palette)
        qapp.setFont(previous_font)


def rendered_color(widget, x, y):
    image = widget.grab().toImage()
    assert not image.isNull()
    scale = image.devicePixelRatio()
    return image.pixelColor(round(x * scale), round(y * scale)).name()


@pytest.mark.parametrize("orientation", ["white", "black"])
def test_brown_board_is_unchanged_by_the_existing_theme_toggle(themed_window, orientation):
    window = themed_window
    window.board.orientation = orientation
    toggle = window.theme_button
    # D4 and E4 contain no pieces or move highlights in the initial position.
    for theme, button_label in (("light", "Dark mode"), ("dark", "Light mode"), ("light", "Dark mode")):
        if window.theme != theme:
            toggle.click()
        assert window.theme == theme
        assert toggle.isVisible()
        assert toggle.text() == button_label
        appearance_buttons = [button for button in window.findChildren(QPushButton)
                              if button.text() in {"Light", "Dark", "Light mode", "Dark mode"}]
        assert appearance_buttons == [toggle]
        for square, expected in ((chess.D4, "#b58863"), (chess.E4, "#f0d9b5")):
            center = window.board.square_rect(square).center()
            assert rendered_color(window.board, center.x(), center.y()) == expected


@pytest.mark.parametrize("theme,disabled_background", [("light", "#e6e6e6"), ("dark", "#262626")])
def test_disabled_primary_play_button_uses_disabled_background(themed_window, theme, disabled_background):
    window = themed_window
    if window.theme != theme:
        window.theme_button.click()
    button = window.play_button
    assert button.objectName() == "primary"
    assert not button.isEnabled()  # No PGN is loaded in this temporary window.
    # Sample clear fill above the text, away from the rounded corners/border.
    assert rendered_color(button, button.width() / 2, 6) == disabled_background
    button.setEnabled(True)
    assert rendered_color(button, button.width() / 2, 6) == "#007bff"
    button.setEnabled(False)
    assert rendered_color(button, button.width() / 2, 6) == disabled_background
