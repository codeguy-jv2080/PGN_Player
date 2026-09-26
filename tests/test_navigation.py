from pathlib import Path

import chess
import pytest

from pgn_player.pgn_loader import FileChangedError, PgnLoadError, index_pgn
from pgn_player.playback import PlaybackController


FIXTURES = Path(__file__).parent / "fixtures"


def player_for(name):
    player = PlaybackController()
    player.set_collection(index_pgn(FIXTURES / name))
    return player


def test_empty_navigation_is_safe():
    player = PlaybackController()
    assert player.board == chess.Board()
    assert player.game_index == -1
    assert not any((player.first(), player.previous(), player.next(), player.last(), player.next_game(), player.return_mainline()))


def test_first_last_and_one_ply_navigation():
    player = player_for("mainline.pgn")
    assert player.path == ()
    assert not player.previous()
    assert player.next()
    assert player.board.peek() == chess.Move.from_uci("e2e4")
    assert player.path == (0,)
    assert player.previous()
    assert player.board == chess.Board()
    assert player.last()
    assert len(player.path) == 10
    assert not player.next()
    assert player.first()
    assert player.path == ()


def test_variation_jumps_return_to_mainline_and_invalid_paths_are_atomic():
    player = player_for("annotations_variations.pgn")
    assert player.go_to((1, 1, 0))
    assert player.node.san() == "c4"
    assert player.board.piece_at(chess.F6) == chess.Piece(chess.KNIGHT, chess.BLACK)
    position = player.board.fen()
    assert not player.go_to((1, 99))
    assert player.path == (1, 1, 0)
    assert player.board.fen() == position
    assert player.return_mainline()
    assert player.path == (0, 0, 0)
    assert player.node.san() == "Nf3"


def test_last_follows_the_selected_variation():
    player = player_for("annotations_variations.pgn")
    player.go_to((1,))
    player.last()
    assert player.path == (1, 0, 0)
    assert player.node.san() == "c4"


def test_game_boundaries_and_fen_reset():
    player = player_for("multiple_games.pgn")
    assert not player.previous_game()
    player.last()
    assert player.next_game()
    assert player.game_index == 1 and player.path == ()
    assert player.board == chess.Board()
    assert player.select_game(2)
    assert not player.next_game()
    assert player.previous_game()
    assert not player.select_game(99)
    assert player.game_index == 1
    player.set_collection(index_pgn(FIXTURES / "special_positions.pgn"))
    assert player.board.piece_at(chess.A7) == chess.Piece(chess.PAWN, chess.WHITE)


def test_empty_collection_clears_previous_state(tmp_path):
    path = tmp_path / "empty.pgn"
    path.write_text("")
    player = player_for("mainline.pgn")
    player.last()
    player.set_collection(index_pgn(path))
    assert player.game is None and player.node is None
    assert player.path == () and player.game_index == -1


def test_initial_invalid_fen_skips_to_readable_game(tmp_path):
    path = tmp_path / "damaged-first.pgn"
    path.write_text('[SetUp "1"]\n[FEN "bad"]\n\n*\n\n[Event "Readable"]\n\n1. d4 d5 *\n')
    collection = index_pgn(path)
    player = PlaybackController()
    player.set_collection(collection)
    assert player.collection is collection
    assert player.game_index == 1
    assert player.game.headers["Event"] == "Readable"
    assert player.next() and player.node.san() == "d4"
    assert collection.errors[0]
    assert "Skipped unreadable initial game(s): 1." in collection.warnings


def test_all_invalid_and_changed_collections_preserve_previous_state(tmp_path):
    player = player_for("mainline.pgn")
    player.next()
    original_game = player.game
    original_collection = player.collection
    original_fen = player.board.fen()
    path = tmp_path / "bad-only.pgn"
    path.write_text('[SetUp "1"]\n[FEN "bad"]\n\n*\n')
    collection = index_pgn(path)
    with pytest.raises(PgnLoadError, match="None of the 1"):
        player.set_collection(collection)
    assert player.game is original_game and player.collection is original_collection
    assert player.path == (0,) and player.board.fen() == original_fen
    path.write_text('[Event "Now changed"]\n\n1. e4 *\n')
    with pytest.raises(FileChangedError):
        player.set_collection(collection)
    assert player.game is original_game and player.collection is original_collection


def test_failed_game_selection_preserves_selected_position(tmp_path):
    path = tmp_path / "damaged-second.pgn"
    path.write_text('[Event "Readable"]\n\n1. d4 *\n\n[SetUp "1"]\n[FEN "bad"]\n\n*\n')
    player = PlaybackController()
    player.set_collection(index_pgn(path))
    player.next()
    previous_game, previous_node = player.game, player.node
    with pytest.raises(PgnLoadError):
        player.next_game()
    assert player.game is previous_game and player.node is previous_node
    assert player.game_index == 0 and player.path == (0,)
