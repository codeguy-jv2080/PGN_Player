"""Offscreen checks that hiding notation changes presentation, not playback."""
import chess
import pytest
from PySide6.QtCore import Qt

from pgn_player.paths import resolve_paths
from pgn_player.storage import SettingsStore
from pgn_player.ui.main_window import MainWindow
import pgn_player.ui.main_window as window_module


LONG_LINE = "1. Nf3 (1. d4 d5 2. c4) Nf6 2. Ng1 Ng8 " + " ".join(
    f"{2 * index + 1}. Nf3 Nf6 {2 * index + 2}. Ng1 Ng8" for index in range(1, 12)
) + " 25. a3 {Final study note} *"
PGN = '''[Event "First study"]
[White "Ada"]
[Black "Ben"]
[Result "*"]

1. e4 {SECRET answer explanation} (1. d4 d5) e5 2. Nf3 Nc6 *

[Event "Long study"]
[White "Cora"]
[Black "Drew"]
[Result "*"]

''' + LONG_LINE + "\n"


@pytest.fixture
def make_window(qtbot, tmp_path, qapp):
    assert qapp.platformName() == "offscreen"
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
        qtbot.waitUntil(lambda: not window._loading and window._worker is None, timeout=10000)
        assert window.player.collection is not None, window._status_error
        qapp.processEvents()
        return window

    yield create
    for window, store in reversed(windows):
        if store._connection is not None:
            window.close()
            qtbot.waitUntil(lambda: window._worker is None, timeout=5000)
            qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
            store.close()


def assert_visibility(window, visible):
    assert window.notation_panel.isVisible() is visible
    assert window.notation.isVisible() is visible
    assert window.players_label.isVisible() is visible
    assert window.metadata.isVisible() is visible
    assert window.notation_action.text() == "Show notation"
    assert window.notation_action.isChecked() is visible
    assert window.notation_button.isVisible()
    assert window.notation_button.text() == ("Hide notation" if visible else "Show notation")
    assert window.store.get_settings()["notation_visible"] is visible


def snapshot(window):
    player = window.player
    return (player.collection, player.game, player.node, player.board,
            player.game_index, player.path, player.board.fen())


def assert_position_unchanged(window, previous):
    current = snapshot(window)
    assert all(before is after for before, after in zip(previous[:4], current[:4]))
    assert current[4:] == previous[4:]


def splitter_ratio(window):
    sizes = window.main_splitter.sizes()
    assert all(size > 0 for size in sizes)
    return sizes[0] / sum(sizes)


def close_store(window, qtbot):
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    window.store.close()


def test_notation_button_and_menu_sync_and_give_board_sidebar_space(make_window, qtbot, qapp):
    window = make_window()
    assert_visibility(window, True)
    width = window.board.width()
    qtbot.mouseClick(window.notation_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert_visibility(window, False)
    assert window.board.width() > width
    assert window.main_splitter.sizes()[1] == 0
    assert window.next_button.isVisible()
    assert window.play_button.isVisible()
    assert window.guess_button.isVisible()
    window.notation_action.trigger()
    qapp.processEvents()
    assert_visibility(window, True)
    qapp.setActiveWindow(window)
    window.notation.setFocus()
    qtbot.waitUntil(lambda: qapp.focusWidget() is window.notation)
    window.notation_action.trigger()
    assert_visibility(window, False)
    assert qapp.focusWidget() is window.notation_button
    qtbot.mouseClick(window.notation_button, Qt.MouseButton.LeftButton)
    assert_visibility(window, True)


def test_hidden_and_visible_preferences_restore_with_splitter_ratio(make_window, qtbot, qapp):
    window = make_window()
    window.resize(1400, 900)
    qapp.processEvents()
    window.main_splitter.setSizes([830, 440])
    ratio = splitter_ratio(window)
    window._set_notation_visible(False)
    window.resize(1600, 900)
    qapp.processEvents()
    window._set_notation_visible(True)
    qapp.processEvents()
    assert splitter_ratio(window) == pytest.approx(ratio, abs=.025)
    window._set_notation_visible(False)
    close_store(window, qtbot)

    hidden = make_window()
    assert_visibility(hidden, False)
    hidden._set_notation_visible(True)
    qapp.processEvents()
    assert_visibility(hidden, True)
    assert splitter_ratio(hidden) == pytest.approx(ratio, abs=.025)
    close_store(hidden, qtbot)

    visible = make_window()
    assert_visibility(visible, True)
    assert splitter_ratio(visible) == pytest.approx(ratio, abs=.025)


def test_hide_show_keeps_autoplay_timer_and_variation_without_reload(make_window, monkeypatch):
    window = make_window()
    window._navigate(lambda: window.player.select_game(1))
    window._navigate(lambda: window.player.go_to((1, 0)))
    window.speed.setValue(30)
    window.toggle_play()
    assert window.autoplay.playing
    previous = snapshot(window)
    timer_id = window._play_timer.timerId()
    interval = window._play_timer.interval()

    def forbidden(*args, **kwargs):
        raise AssertionError("Visibility changes must not reopen or reindex the PGN")

    with monkeypatch.context() as patches:
        patches.setattr(window, "open_file", forbidden)
        patches.setattr(window_module, "open_pgn", forbidden)
        patches.setattr(window_module, "index_pgn", forbidden)
        patches.setattr(window.player.collection, "load_game", forbidden)
        for visible in (False, True):
            window._set_notation_visible(visible)
            assert_position_unchanged(window, previous)
            assert window.autoplay.playing
            assert window._play_timer.isActive()
            assert window._play_timer.timerId() == timer_id
            assert window._play_timer.interval() == interval


def test_navigation_theme_and_font_while_hidden_show_current_move(make_window, qapp):
    window = make_window()
    window._set_notation_visible(False)
    window._navigate(lambda: window.player.select_game(1))
    window._navigate(window.player.last)
    assert window.player.node.san() == "a3"
    window.toggle_theme()
    window._set_notation_font_size(18)
    previous = snapshot(window)
    window._set_notation_visible(True)
    qapp.processEvents()
    assert_position_unchanged(window, previous)
    assert_visibility(window, True)
    assert "Cora" in window.players_label.text()
    assert "Final study note" in window.notation.toPlainText()
    assert window.theme == "dark"
    assert window.notation.font_size == 18
    selected = window.notation.document().find("a3")
    assert not selected.isNull()
    assert selected.charFormat().fontPointSize() == 18
    assert selected.charFormat().background().color().name() == "#007bff"
    assert window.notation.viewport().rect().intersects(window.notation.cursorRect(selected))


def test_guess_remains_active_and_playable_with_hidden_notation(make_window):
    window = make_window()
    window.guess_variations.setChecked(True)
    window._toggle_guess(True)
    window._board_move(chess.E2, chess.E4)
    assert window._opponent_timer.isActive()
    timer_id = window._opponent_timer.timerId()
    previous = snapshot(window)
    for visible in (False, True, False):
        window._set_notation_visible(visible)
        assert_position_unchanged(window, previous)
        assert window.guess.mode == "white"
        assert window.guess.correct == 1
        assert window.guess.incorrect == 0
        assert window.guess.include_variations
        assert window.guess_button.isChecked()
        assert window.guess_panel.isVisible()
        assert window._opponent_timer.isActive()
        assert window._opponent_timer.timerId() == timer_id
        assert not window.board.input_enabled
        for hidden in ("SECRET", "e5", "Nf3", "d4"):
            assert hidden not in window.notation.toPlainText()

    # Complete the recorded reply as a single-shot timer would, while hidden.
    window._opponent_timer.stop()
    window._opponent_tick()
    assert window.player.path == (0, 0)
    assert window.board.input_enabled
    window._board_move(chess.G1, chess.F3)
    assert window.guess.correct == 2
    assert window.player.path == (0, 0, 0)
    window._set_notation_visible(True)
    assert window.guess.mode == "white"
    assert window.guess.correct == 2
    assert window._opponent_timer.isActive()
    assert "Nf3" in window.notation.toPlainText()
    assert "Nc6" not in window.notation.toPlainText()
    assert "SECRET" not in window.notation.toPlainText()
