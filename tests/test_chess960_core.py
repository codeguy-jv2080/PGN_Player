"""Synthetic Chess960 edge cases through the existing PGN/player controllers."""

import io

import chess
import chess.pgn
import pytest

from pgn_player.autoplay import AutoplayController
from pgn_player.guess_move import GuessMoveController
from pgn_player.pgn_loader import index_pgn, open_pgn
from pgn_player.playback import PlaybackController
from pgn_player.storage import SettingsStore


ANNOTATED_960 = '''[Event "Synthetic Chess960 variations"]
[Variant "Chess960"]
[SetUp "1"]
[FEN "4k3/8/8/8/8/8/8/1R2K1R1 w GB - 0 1"]
[Result "*"]

{Root comment} 1. O-O $1 {Short castle}
(1. O-O-O $5 {Long castle} 1... Kf7 (1... Ke7 {Nested reply}) 2. Kb2)
1... Kd7 2. Kf2 *
'''
STANDARD = '''[Event "Synthetic standard castling"]
[SetUp "1"]
[FEN "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"]
[Result "*"]

1. O-O O-O-O *
'''


def _player(tmp_path, text):
    source = tmp_path / "synthetic-chess960.pgn"
    source.write_text(text, encoding="utf-8")
    player = PlaybackController()
    player.set_collection(index_pgn(source))
    return player


def _castling_board(king_file, rook_file, color=chess.WHITE):
    board = chess.Board(None, chess960=True)
    board.set_piece_at(chess.square(king_file, 0), chess.Piece(chess.KING, chess.WHITE))
    board.set_piece_at(chess.square(rook_file, 0), chess.Piece(chess.ROOK, chess.WHITE))
    # Put the other king off the rook's file so the starting position is legal.
    board.set_piece_at(chess.H8 if rook_file == 0 else chess.A8, chess.Piece(chess.KING, chess.BLACK))
    board.castling_rights = chess.BB_SQUARES[chess.square(rook_file, 0)]
    if color == chess.BLACK:
        board = board.mirror()
    assert board.is_valid()
    return board


def _pgn(board, movetext):
    return (
        '[Event "Synthetic castling"]\n[Variant "Chess960"]\n[SetUp "1"]\n'
        f'[FEN "{board.shredder_fen()}"]\n\n{movetext} *\n'
    )


@pytest.mark.parametrize("color", [chess.WHITE, chess.BLACK], ids=["white", "black"])
@pytest.mark.parametrize(
    "king_file,rook_file",
    [(6, 7), (2, 0), (4, 5), (4, 3), (5, 6), (3, 2), (4, 6), (4, 2), (5, 7), (1, 0)],
    ids=["king-stays-short", "king-stays-long", "rook-stays-short", "rook-stays-long",
         "swap-short", "swap-long", "overlap-short", "overlap-long", "king-one-short", "king-one-long"],
)
def test_unusual_castling_load_navigation_and_guess(tmp_path, king_file, rook_file, color):
    initial = _castling_board(king_file, rook_file, color)
    short = rook_file > king_file
    san = "O-O" if short else "O-O-O"
    move = initial.parse_san(san)
    assert initial.is_castling(move) and not initial.is_capture(move)
    player = _player(tmp_path, _pgn(initial, san))
    assert not player.game.errors
    assert player.board.chess960
    assert player.board.fen() == initial.fen()
    assert player.next() and player.node.san() == san
    rank = 0 if color else 7
    assert player.board.king(color) == chess.square(6 if short else 2, rank)
    assert player.board.piece_at(chess.square(5 if short else 3, rank)) == chess.Piece(chess.ROOK, color)
    final = player.board.fen()
    assert not player.next()
    assert player.previous() and player.board.fen() == initial.fen()
    assert player.board.castling_rights == initial.castling_rights
    assert not player.previous()
    guess = GuessMoveController(player)
    guess.start("white" if color else "black")
    result = guess.submit(move)
    assert result.status == "correct" and result.played_san == san
    assert guess.guesses == guess.correct == 1 and guess.incorrect == 0
    assert player.board.fen() == final
    assert player.first() and player.board.fen() == initial.fen()
    assert player.last() and player.board.fen() == final


@pytest.mark.parametrize("color", [chess.WHITE, chess.BLACK], ids=["white", "black"])
@pytest.mark.parametrize("restriction", ["in-check", "crosses-check", "lands-in-check", "blocked", "lost-rights"])
def test_prohibited_chess960_castling_is_rejected_without_score(tmp_path, color, restriction):
    board = _castling_board(4, 7)
    if restriction in {"in-check", "crosses-check", "lands-in-check"}:
        square = {"in-check": chess.E8, "crosses-check": chess.F8, "lands-in-check": chess.G8}[restriction]
        board.set_piece_at(square, chess.Piece(chess.ROOK, chess.BLACK))
    elif restriction == "blocked":
        board.set_piece_at(chess.F1, chess.Piece(chess.KNIGHT, chess.WHITE))
    else:
        board.castling_rights = chess.BB_EMPTY
    if color == chess.BLACK:
        board = board.mirror()
    assert board.is_valid()
    rank = 0 if color else 7
    castle = chess.Move(chess.square(4, rank), chess.square(7, rank))
    assert castle not in board.legal_moves
    # A legal recorded continuation makes the trainer active, even in check.
    continuation = next(iter(board.legal_moves))
    player = _player(tmp_path, _pgn(board, board.san(continuation)))
    assert not player.game.errors
    guess = GuessMoveController(player)
    guess.start("both")
    before = player.board.fen()
    assert guess.submit(castle).status == "illegal"
    assert guess.guesses == 0 and guess.pending_answer is None
    assert player.path == () and player.board.fen() == before


def _tree(game):
    def visit(node, path):
        return [(path, node.move, node.comment, node.starting_comment, node.nags,
                 node.board().fen(), node.board().chess960)] + [
            item for i, child in enumerate(node.variations) for item in visit(child, path + (i,))
        ]
    return visit(game, ())


@pytest.mark.parametrize("separator", ["\n", ""], ids=["blank-separated", "adjacent-headers"])
def test_mixed_index_cache_export_and_nested_variations(tmp_path, separator):
    source = tmp_path / "mixed.pgn"
    # Put Chess960 both before and after a standard game to detect leaked state.
    source.write_text(separator.join([ANNOTATED_960, STANDARD, ANNOTATED_960]), encoding="utf-8")
    original = source.read_bytes()
    cache = tmp_path / "index.sqlite3"
    first = open_pgn(source, cache_path=cache)
    cached = open_pgn(source, cache_path=cache)
    assert not first.from_cache and cached.from_cache
    assert len(first) == len(cached) == 3
    for index in (2, 1, 0):
        game = cached.load_game(index)
        assert not game.errors
        assert game.board().chess960 == (index != 1)
        assert _tree(game) == _tree(first.load_game(index))
        assert cached.entries[index].headers["FEN"] == game.headers["FEN"]
        exported = game.accept(chess.pgn.StringExporter(headers=True, comments=True, variations=True))
        restored = chess.pgn.read_game(io.StringIO(exported))
        assert not restored.errors
        assert dict(restored.headers) == dict(game.headers)
        assert _tree(restored) == _tree(game)
        assert "O-O" in exported and "O-O-O" in exported
        if index != 1:
            assert 'w GB - 0 1' in exported
            assert game.comment == "Root comment"
            assert game.variations[0].nags == {chess.pgn.NAG_GOOD_MOVE}
            assert game.variations[1].nags == {chess.pgn.NAG_SPECULATIVE_MOVE}
            assert game.variations[1].variations[1].comment == "Nested reply"
    assert source.read_bytes() == original


def test_chess960_variation_guesses_and_wrong_legal_castle(tmp_path):
    player = _player(tmp_path, ANNOTATED_960)
    guess = GuessMoveController(player)
    guess.start("both")
    original = str(player.game)
    short = player.board.parse_san("O-O")
    long = player.board.parse_san("O-O-O")
    assert guess.submit(long).status == "incorrect"
    assert guess.guesses == guess.incorrect == 1
    assert guess.pending_answer == "O-O" and player.path == ()
    assert guess.submit(short).status == "pending"
    assert guess.continue_recorded()
    assert player.node.san() == "O-O" and player.path == (0,)
    player.first()
    guess.start("both")
    guess.include_variations = True
    assert guess.submit(long).status == "correct"
    assert player.path == (1,)
    assert guess.submit(player.board.parse_san("Ke7")).status == "correct"
    assert player.path == (1, 1) and player.node.comment == "Nested reply"
    assert guess.correct == guess.guesses == 2
    assert str(player.game) == original


@pytest.mark.parametrize("mode", ["white", "black", "both"])
def test_training_colors_advance_recorded_chess960_sequence(tmp_path, mode):
    player = _player(tmp_path, ANNOTATED_960)
    guess = GuessMoveController(player)
    guess.start(mode)
    decisions = 0
    while not player.at_end:
        if guess.waiting_for_guess:
            move = player.node.variations[0].move
            assert guess.submit(move).status == "correct"
            decisions += 1
        else:
            assert guess.play_opponent()
        assert player.board.fen() == player.node.board().fen()
        assert player.board.chess960
    assert decisions == {"white": 2, "black": 1, "both": 3}[mode]
    assert guess.correct == decisions and guess.incorrect == 0


@pytest.mark.parametrize("continue_next,loop", [(False, False), (True, False), (False, True), (True, True)])
def test_chess960_autoplay_loop_boundaries_and_pause(tmp_path, continue_next, loop):
    player = _player(tmp_path, ANNOTATED_960 + "\n" + STANDARD)
    autoplay = AutoplayController(player)
    autoplay.continue_next, autoplay.loop = continue_next, loop
    autoplay.delay_seconds, autoplay.between_games_seconds = 0.25, 1.25
    cycle = [("move", 0, (0,)), ("move", 0, (0, 0)), ("move", 0, (0, 0, 0))]
    if continue_next:
        cycle += [("game", 1, ()), ("move", 1, (0,)), ("move", 1, (0, 0))]
    expected = cycle + [("game", 0, ())] + cycle if loop else cycle
    assert autoplay.start()
    for event, index, path in expected:
        assert autoplay.next_delay == (1.25 if event == "game" else 0.25)
        autoplay.pause()
        before = (player.game_index, player.path, player.board.fen())
        assert autoplay.step() == "stop"
        assert before == (player.game_index, player.path, player.board.fen())
        assert autoplay.start()
        assert autoplay.step() == event
        assert (player.game_index, player.path) == (index, path)
        assert player.board.chess960 == (index == 0)
        assert player.board.fen() == player.node.board().fen()
    assert autoplay.playing == loop
    autoplay.pause()


def test_chess960_autoplay_variations_and_persistent_resume(tmp_path):
    player = _player(tmp_path, ANNOTATED_960 + "\n" + STANDARD)
    autoplay = AutoplayController(player)
    autoplay.include_variations = True
    autoplay.continue_next = False
    assert autoplay.start()
    seen = []
    while autoplay.playing:
        assert autoplay.step() == "move"
        seen.append(player.path)
        assert player.board.fen() == player.node.board().fen() and player.board.chess960
    assert seen == [(0,), (0, 0), (0, 0, 0), (1,), (1, 0), (1, 0, 0), (1, 1)]
    assert player.go_to((1, 0, 0))
    expected = player.board.fen()
    source, fingerprint = player.collection.path, player.collection.fingerprint
    db = tmp_path / "settings.sqlite3"
    with SettingsStore(db) as store:
        store.save_resume(source, fingerprint, player.game_index, player.path, "black")
    player.next_game()
    assert not player.board.chess960
    with SettingsStore(db) as store:
        saved = store.load_resume(source, fingerprint)
    assert saved["orientation"] == "black"
    restored = PlaybackController()
    restored.set_collection(index_pgn(source))
    assert restored.select_game(saved["game_index"])
    assert restored.go_to(saved["variation_path"])
    assert restored.path == (1, 0, 0) and restored.board.fen() == expected
    assert restored.board.chess960
    assert restored.previous() and restored.previous() and restored.previous()
    assert restored.board.fen() == restored.game.board().fen()
    assert restored.board.castling_rights == chess.BB_B1 | chess.BB_G1
    assert not restored.previous_game()
    assert restored.next_game() and not restored.next_game()
    assert not restored.board.chess960
