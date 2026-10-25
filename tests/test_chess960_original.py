"""Opt-in audit of the user's unchanged ten-game Chess960 collection.

Set PGN_PLAYER_CHESS960_PGN to its location. Neither the source PGN nor its
private location is included in the repository. The reference games are read
directly by python-chess, independently of the application's index/seek layer.
"""

import hashlib
from io import StringIO
import os
from pathlib import Path

import chess
import chess.pgn
import pytest

from pgn_player.autoplay import AutoplayController
from pgn_player.pgn_loader import index_pgn, open_pgn
from pgn_player.playback import PlaybackController


ORIGINAL_SHA256 = "a011d631cdb15c4bc7607659b178ffcbf6dbad2beb50b949486e3d3c69a56230"
EXPECTED_PLIES = (81, 88, 54, 107, 143, 100, 92, 85, 49, 65)
EXPECTED_RESULTS = ("1-0", "0-1", "0-1", "1-0", "1-0", "0-1", "0-1", "1-0", "1-0", "1-0")
EXPECTED_CASTLES = (
    ((8, "O-O"), (17, "O-O")),
    ((9, "O-O"), (10, "O-O")),
    (),
    ((15, "O-O"), (26, "O-O-O")),
    ((14, "O-O"), (15, "O-O")),
    ((20, "O-O-O"),),
    ((16, "O-O"), (17, "O-O")),
    (),
    ((5, "O-O"), (6, "O-O")),
    ((23, "O-O"),),
)


def _tree(node):
    return (
        node.move, node.comment, node.starting_comment, frozenset(node.nags),
        tuple(_tree(child) for child in node.variations),
    )


@pytest.fixture(scope="module")
def original_chess960():
    location = os.environ.get("PGN_PLAYER_CHESS960_PGN")
    if not location:
        pytest.skip("Set PGN_PLAYER_CHESS960_PGN to run the original Chess960 audit.")
    path = Path(location)
    raw = path.read_bytes()
    assert len(raw) == 8_211
    assert hashlib.sha256(raw).hexdigest() == ORIGINAL_SHA256, "This audit requires the unchanged original PGN."
    stream = StringIO(raw.decode("utf-8-sig"))
    references = []
    while (game := chess.pgn.read_game(stream)) is not None:
        assert not game.errors
        assert game.headers["Variant"] == "Chess960"
        assert game.headers["SetUp"] == "1"
        assert game.board().chess960
        assert game.board().board_fen() != chess.Board().board_fen()
        references.append(game)
    assert len(references) == 10
    for index, game in enumerate(references):
        board = game.board()
        castles = []
        for ply, move in enumerate(game.mainline_moves(), 1):
            assert move in board.legal_moves
            if board.is_castling(move):
                castles.append((ply, board.san(move)))
                assert not board.is_capture(move)
            board.push(move)
        assert ply == EXPECTED_PLIES[index]
        assert game.headers["Result"] == EXPECTED_RESULTS[index]
        assert tuple(castles) == EXPECTED_CASTLES[index]
    try:
        yield path, references
    finally:
        assert path.read_bytes() == raw, "The original Chess960 PGN must never change."


def test_original_chess960_index_cache_and_all_ten_complete_trees(original_chess960, tmp_path):
    path, references = original_chess960
    cache = tmp_path / "index.sqlite3"
    first = open_pgn(path, cache_path=cache)
    assert len(first) == 10 and not first.from_cache
    collection = open_pgn(path, cache_path=cache)
    assert collection.from_cache
    assert collection.entries == first.entries
    assert collection.fingerprint == first.fingerprint == ORIGINAL_SHA256
    assert len({entry.offset for entry in collection.entries}) == 10
    # More than the eight-game parsed cache, including reverse seeks.
    for index in (*range(10), *reversed(range(10))):
        actual = collection.load_game(index)
        expected = references[index]
        assert not actual.errors and collection.errors[index] == []
        assert collection.entries[index].headers == dict(expected.headers)
        assert dict(actual.headers) == dict(expected.headers)
        assert _tree(actual) == _tree(expected)
        assert actual.board().chess960
        assert actual.board().fen(shredder=True) == expected.board().fen(shredder=True)
        assert actual.end().board().fen(shredder=True) == expected.end().board().fen(shredder=True)


def test_original_chess960_every_forward_and_backward_ply_and_game_boundary(original_chess960):
    path, references = original_chess960
    player = PlaybackController()
    player.set_collection(index_pgn(path))
    assert not player.previous_game()
    moves_seen = 0
    for index, reference in enumerate(references):
        if index:
            assert player.next_game()
        assert player.game_index == index and player.path == ()
        expected_board = reference.board()
        assert player.board.chess960
        assert player.board.fen(shredder=True) == expected_board.fen(shredder=True)
        for ply, move in enumerate(reference.mainline_moves(), 1):
            assert player.next()
            expected_board.push(move)
            assert player.path == (0,) * ply and player.node.move == move
            assert player.board.chess960
            assert player.board.fen(shredder=True) == expected_board.fen(shredder=True)
            moves_seen += 1
        assert not player.next()
        for remaining in reversed(range(EXPECTED_PLIES[index])):
            assert player.previous()
            expected_board.pop()
            assert player.path == (0,) * remaining
            assert player.board.chess960
            assert player.board.fen(shredder=True) == expected_board.fen(shredder=True)
        assert not player.previous()
        assert player.last()
        assert player.board.fen(shredder=True) == reference.end().board().fen(shredder=True)
        assert player.first() and player.path == ()
    assert moves_seen == 864
    assert not player.next_game()
    for index in reversed(range(9)):
        assert player.previous_game()
        assert player.game_index == index and player.path == ()
        assert player.board.fen(shredder=True) == references[index].board().fen(shredder=True)
    assert not player.previous_game()


def test_original_chess960_autoplay_plays_all_moves_and_continues_games(original_chess960):
    path, references = original_chess960
    player = PlaybackController()
    player.set_collection(index_pgn(path))
    autoplay = AutoplayController(player)
    autoplay.continue_next = True
    autoplay.loop = False
    assert autoplay.start()
    moves_seen = transitions_seen = 0
    for index, reference in enumerate(references):
        if index:
            assert autoplay.next_delay == autoplay.between_games_seconds
            assert autoplay.step() == "game"
            transitions_seen += 1
        assert player.game_index == index and player.path == ()
        expected_board = reference.board()
        for ply, move in enumerate(reference.mainline_moves(), 1):
            assert autoplay.next_delay == autoplay.delay_seconds
            assert autoplay.step() == "move"
            expected_board.push(move)
            assert player.game_index == index and player.path == (0,) * ply
            assert player.node.move == move
            assert player.board.chess960
            assert player.board.fen(shredder=True) == expected_board.fen(shredder=True)
            moves_seen += 1
    assert moves_seen == 864 and transitions_seen == 9
    assert not autoplay.playing and autoplay.step() == "stop"
    assert not autoplay.start()


def test_original_chess960_window_exports_and_reimports_all_games(original_chess960, qtbot, tmp_path, monkeypatch):
    from pgn_player.paths import resolve_paths
    from pgn_player.storage import SettingsStore
    from pgn_player.ui.main_window import MainWindow, QFileDialog

    source, references = original_chess960
    paths = resolve_paths("development", app_dir=tmp_path, data_override=tmp_path / "state")
    with SettingsStore(paths.db_path) as store:
        window = MainWindow(store, paths)
        qtbot.addWidget(window)
        try:
            window.open_file(source, restore=False)
            qtbot.waitUntil(lambda: window._worker is None, timeout=10000)
            assert window.player.collection is not None, window._status_error
            assert window.game_list.model.rowCount() == 10
            for index, reference in enumerate(references):
                window._navigate(lambda index=index: window.player.select_game(index))
                assert window.board.board.chess960
                assert window.board.board.fen(shredder=True) == reference.board().fen(shredder=True)
                window._navigate(window.player.last)
                assert window.board.board.fen(shredder=True) == reference.end().board().fen(shredder=True)
                destination = tmp_path / f"export-{index + 1}.pgn"
                monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, destination=destination: (str(destination), ""))
                window.export_game()
                assert destination.is_file(), window._status_error
                exported = index_pgn(destination)
                assert len(exported) == 1
                game = exported.load_game(0)
                assert not game.errors
                assert dict(game.headers) == dict(reference.headers)
                assert _tree(game) == _tree(reference)
                assert game.board().chess960
                assert game.board().fen(shredder=True) == reference.board().fen(shredder=True)
                assert game.end().board().fen(shredder=True) == reference.end().board().fen(shredder=True)
        finally:
            window.close()
            qtbot.waitUntil(lambda: window._worker is None, timeout=10000)


def test_original_chess960_saved_positions_restore_before_and_after_every_castle(original_chess960, tmp_path):
    from pgn_player.storage import SettingsStore

    source, references = original_chess960
    collection = index_pgn(source)
    database = tmp_path / "settings.sqlite3"
    positions_seen = 0
    for index, castles in enumerate(EXPECTED_CASTLES):
        for ply, _ in castles:
            for depth in (ply - 1, ply):
                orientation = "white" if depth % 2 else "black"
                with SettingsStore(database) as store:
                    store.save_resume(source, collection.fingerprint, index, (0,) * depth, orientation)
                with SettingsStore(database) as store:
                    saved = store.load_resume(source, collection.fingerprint)
                assert saved is not None and saved["orientation"] == orientation
                restored = PlaybackController()
                restored.set_collection(collection)
                assert restored.select_game(saved["game_index"])
                assert restored.go_to(saved["variation_path"])
                expected = references[index].board()
                for move in list(references[index].mainline_moves())[:depth]:
                    expected.push(move)
                assert restored.board.chess960
                assert restored.board.fen(shredder=True) == expected.fen(shredder=True)
                positions_seen += 1
    assert positions_seen == 28
