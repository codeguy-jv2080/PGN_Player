"""Offscreen recent-menu actions preserve the active viewing/training session."""
import chess
import pytest

from pgn_player.paths import resolve_paths
from pgn_player.storage import SettingsStore, StorageError
from pgn_player.ui.main_window import MainWindow


PGN = '[Event "Recent study"]\n\n1. e4 {SECRET hint} e5 2. Nf3 Nc6 *\n'


@pytest.fixture
def make_window(qtbot, tmp_path, qapp):
    assert qapp.platformName() == "offscreen"
    paths = resolve_paths("development", app_dir=tmp_path, data_override=tmp_path / "state")
    source = tmp_path / "study.pgn"
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
        window._populate_recent()
        return window

    yield create
    for window, store in reversed(windows):
        if store._connection is not None:
            window.close()
            qtbot.waitUntil(lambda: window._worker is None, timeout=5000)
            qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
            store.close()


def clear_action(window):
    return next(action for action in window.recent_menu.actions() if action.text() == "Clear Recent Files")


def snapshot(window):
    player = window.player
    return (player.collection, player.game, player.node, player.board, window.board.board,
            player.path, player.board.fen(), window.notation.toPlainText())


def assert_same_position(window, previous):
    current = snapshot(window)
    assert all(before is after for before, after in zip(previous[:5], current[:5]))
    assert current[5:] == previous[5:]


def test_empty_recent_menu_disables_placeholder_and_clear_action(make_window):
    window = make_window(load=False)
    actions = window.recent_menu.actions()
    assert [action.text() for action in actions if not action.isSeparator()] == ["No recent files", "Clear Recent Files"]
    assert not actions[0].isEnabled()
    assert not clear_action(window).isEnabled()
    assert any(action.isSeparator() for action in actions)


def test_clear_menu_action_preserves_playback_and_stays_empty_after_restart(make_window, qtbot):
    window = make_window()
    source = window.player.collection.path
    entries = [action for action in window.recent_menu.actions() if action.toolTip() == str(source)]
    assert len(entries) == 1 and entries[0].text() == "study.pgn"
    assert clear_action(window).isEnabled()
    window._navigate(window.player.next)
    window.speed.setValue(30)
    window.toggle_play()
    timer_id = window._play_timer.timerId()
    previous = snapshot(window)
    clear_action(window).trigger()
    assert window.store.recent_files() == []
    assert not clear_action(window).isEnabled()
    assert_same_position(window, previous)
    assert window.autoplay.playing
    assert window._play_timer.isActive()
    assert window._play_timer.timerId() == timer_id
    assert window.store.get("last_file") == str(source)
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    window.store.close()

    reopened = make_window(load=False)
    assert reopened.store.recent_files() == []
    assert not clear_action(reopened).isEnabled()
    assert reopened.store.get("last_file") == str(source)


def test_failed_and_successful_clear_leave_guess_session_and_reply_timer_running(make_window, monkeypatch):
    window = make_window()
    window._toggle_guess(True)
    window._board_move(chess.E2, chess.E4)
    assert window._opponent_timer.isActive()
    timer_id = window._opponent_timer.timerId()
    previous = snapshot(window)
    history = window.store.recent_files()

    def fail():
        raise StorageError("Recent-file database is locked")

    with monkeypatch.context() as patches:
        patches.setattr(window.store, "clear_recent_files", fail)
        clear_action(window).trigger()
        assert "locked" in window._status_error
        assert window.store.recent_files() == history
        assert clear_action(window).isEnabled()
        assert_same_position(window, previous)
        assert window.guess.mode == "white"
        assert window._opponent_timer.timerId() == timer_id
    clear_action(window).trigger()
    assert window.store.recent_files() == []
    assert not clear_action(window).isEnabled()
    assert_same_position(window, previous)
    assert window.guess.mode == "white"
    assert window.guess.correct == 1
    assert window.guess.incorrect == 0
    assert window.guess_panel.isVisible()
    assert window._opponent_timer.isActive()
    assert window._opponent_timer.timerId() == timer_id
    assert "SECRET" not in window.notation.toPlainText()


def test_unreadable_history_shows_unavailable_menu_without_clearing_entries(make_window, monkeypatch):
    window = make_window()
    history = window.store.recent_files()

    def fail():
        raise StorageError("Cannot read recent files")

    with monkeypatch.context() as patches:
        patches.setattr(window.store, "recent_files", fail)
        window._populate_recent()
        actions = window.recent_menu.actions()
        assert [action.text() for action in actions if not action.isSeparator()] == ["Recent files unavailable", "Clear Recent Files"]
        assert not actions[0].isEnabled()
        assert not clear_action(window).isEnabled()
        assert "Cannot read recent files" in window._status_error
    assert window.store.recent_files() == history
