from pathlib import Path

import chess
import pytest

from pgn_player.guess_move import GuessMoveController
from pgn_player.pgn_loader import index_pgn
from pgn_player.playback import PlaybackController


FIXTURES = Path(__file__).parent / "fixtures"


def setup(name="annotations_variations.pgn", mode="both"):
    player = PlaybackController()
    player.set_collection(index_pgn(FIXTURES / name))
    guess = GuessMoveController(player)
    guess.start(mode)
    return player, guess


def test_correct_both_sides_move_advances_exactly_one_ply():
    player, guess = setup()
    assert guess.waiting_for_guess
    result = guess.submit(chess.Move.from_uci("e2e4"))
    assert result.status == "correct" and result.played_san == "e4"
    assert player.path == (0,)
    assert guess.waiting_for_guess
    assert not guess.play_opponent()
    assert guess.submit(chess.Move.from_uci("e7e5")).status == "correct"
    assert player.path == (0, 0)
    assert guess.correct == guess.guesses == 2
    assert guess.percentage == 100


def test_wrong_legal_move_reveals_answer_without_mutating_tree():
    player, guess = setup()
    before = str(player.game)
    result = guess.submit(chess.Move.from_uci("g1f3"))
    assert result.status == "incorrect" and result.expected_san == "e4"
    assert guess.pending_answer == "e4"
    assert not guess.waiting_for_guess
    assert player.path == ()
    assert guess.incorrect == guess.guesses == 1
    assert guess.submit(chess.Move.from_uci("e2e4")).status == "pending"
    assert guess.guesses == 1
    assert guess.continue_recorded()
    assert player.path == (0,)
    assert guess.pending_answer is None
    assert str(player.game) == before


def test_illegal_moves_do_not_count_or_reveal_answer():
    player, guess = setup()
    result = guess.submit(chess.Move.from_uci("e2e5"))
    assert result.status == "illegal" and not result.expected_san
    assert player.path == () and guess.guesses == 0


def test_white_mode_plays_only_opponent_reply():
    player, guess = setup(mode="white")
    assert not guess.play_opponent()
    guess.submit(chess.Move.from_uci("e2e4"))
    assert not guess.waiting_for_guess
    assert guess.play_opponent()
    assert player.path == (0, 0)
    assert guess.waiting_for_guess
    assert not guess.play_opponent()
    assert guess.guesses == 1


def test_black_mode_plays_initial_white_move():
    player, guess = setup(mode="black")
    assert not guess.waiting_for_guess
    assert guess.play_opponent()
    assert guess.waiting_for_guess
    assert guess.submit(chess.Move.from_uci("e7e5")).status == "correct"
    assert guess.play_opponent()
    assert player.node.san() == "Nf3"


def test_variations_accepted_only_when_enabled():
    player, guess = setup()
    assert guess.submit(chess.Move.from_uci("d2d4")).status == "incorrect"
    guess.start("both")
    guess.include_variations = True
    assert guess.submit(chess.Move.from_uci("d2d4")).status == "correct"
    assert player.path == (1,)
    assert guess.submit(chess.Move.from_uci("g8f6")).status == "correct"
    assert player.path == (1, 1)


def test_reset_stop_invalid_mode_and_manual_navigation():
    player, guess = setup()
    guess.submit(chess.Move.from_uci("g1f3"))
    player.next()
    assert guess.pending_answer is None
    assert not guess.continue_recorded()
    guess.reset_stats()
    assert guess.guesses == 0 and guess.percentage == 0
    guess.stop()
    assert guess.submit(chess.Move.from_uci("e7e5")).status == "inactive"
    assert not guess.waiting_for_guess
    with pytest.raises(ValueError):
        guess.start("unknown")


def test_special_moves_use_exact_chess_moves():
    player, guess = setup("special_positions.pgn")
    assert guess.submit(chess.Move.from_uci("a7a8n")).status == "incorrect"
    assert guess.pending_answer == "a8=Q+"
    guess.start("both")
    assert guess.submit(chess.Move.from_uci("a7a8q")).status == "correct"
    player.select_game(1)
    guess.start("both")
    assert guess.submit(chess.Move.from_uci("e5d6")).status == "correct"
    player.select_game(4)
    guess.start("black")
    assert guess.waiting_for_guess
    assert guess.submit(chess.Move.from_uci("g3g2")).status == "correct"
    assert guess.submit(chess.Move.from_uci("e1f1")).status == "finished"


def test_statistics_count_each_decision_once_and_keep_percentage():
    player, guess = setup()
    guess.submit(chess.Move.from_uci("e2e4"))
    guess.submit(chess.Move.from_uci("e7e6"))
    guess.submit(chess.Move.from_uci("e7e5"))
    assert guess.guesses == 2
    assert guess.correct == guess.incorrect == 1
    assert guess.percentage == 50
    guess.continue_recorded()
    assert player.path == (0, 0) and guess.guesses == 2
    guess.stop()
    assert guess.guesses == 2


def test_castling_and_black_underpromotion_are_recorded_guesses(tmp_path):
    path = tmp_path / "special-guesses.pgn"
    path.write_text(
        '[SetUp "1"]\n[FEN "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"]\n\n'
        '1. O-O O-O-O *\n\n'
        '[SetUp "1"]\n[FEN "4k3/8/8/8/8/8/p7/7K b - - 0 24"]\n\n'
        '24... a1=N *\n'
    )
    player = PlaybackController()
    player.set_collection(index_pgn(path))
    guess = GuessMoveController(player)
    guess.start("both")
    assert guess.submit(chess.Move.from_uci("e1g1")).status == "correct"
    assert guess.submit(chess.Move.from_uci("e8c8")).status == "correct"
    assert player.board.piece_at(chess.D8) == chess.Piece(chess.ROOK, chess.BLACK)
    player.next_game()
    guess.start("black")
    result = guess.submit(chess.Move.from_uci("a2a1n"))
    assert result.status == "correct" and result.played_san == "a1=N"
