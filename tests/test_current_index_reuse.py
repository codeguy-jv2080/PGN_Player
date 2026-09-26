"""Offscreen coverage for sharing an unchanged, currently loaded PGN index."""
import chess

from pgn_player.index_cache import IndexCache
from pgn_player.paths import resolve_paths
from pgn_player.storage import SettingsStore
from pgn_player.ui.main_window import MainWindow


def test_same_window_reopen_shares_entries_and_preserves_resume_and_guess(tmp_path, qtbot, monkeypatch):
    source = tmp_path / "study.pgn"
    source.write_text(
        '[White "First"]\n\n1. e4 e5 *\n\n'
        '[White "Second"]\n\n1. d4 d5 2. c4 *\n', encoding="utf-8")
    paths = resolve_paths("development", app_dir=tmp_path, data_override=tmp_path / "state")
    store = SettingsStore(paths.db_path)
    window = MainWindow(store, paths)
    qtbot.addWidget(window)
    window.show()
    try:
        window.open_file(source)
        qtbot.waitUntil(lambda: window._worker is None, timeout=5000)
        original = window.player.collection
        assert original is not None
        window._navigate(lambda: window.player.select_game(1))
        window.guess_color.setCurrentIndex(window.guess_color.findData("both"))
        window.guess_variations.setChecked(True)
        window._toggle_guess(True)
        window._board_move(chess.D2, chess.D4)
        window.flip_board()
        previous_game = window.player.game
        position = window.player.board.fen()
        messages = []
        window.statusBar().messageChanged.connect(messages.append)

        def forbidden_load(*args, **kwargs):
            raise AssertionError("Same-window reopening must share the current index")

        monkeypatch.setattr(IndexCache, "load", forbidden_load)
        window.open_file(source)
        qtbot.waitUntil(lambda: window._worker is None, timeout=5000)
        assert window.player.collection is not original
        assert window.player.collection.entries is original.entries
        assert window.game_list.model.entries is original.entries
        assert window.player.game is not previous_game
        assert window.player.game_index == 1 and window.player.path == (0,)
        assert window.player.board.fen() == position and window.board.orientation == "black"
        assert window.guess.mode == "both" and window.guess.correct == 1
        assert window.guess.include_variations and window.guess_panel.isVisible()
        assert window.board.input_enabled
        assert "Reusing loaded index…" in messages
        assert not any(message.startswith(("Indexing ", "Saving PGN index")) for message in messages)
        assert not window.progress.isVisible()
    finally:
        window.close()
        qtbot.waitUntil(lambda: window._worker is None, timeout=5000)
        store.close()
