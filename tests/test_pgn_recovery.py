"""Focused regressions for whitespace and malformed variation recovery."""

import io

import chess
import chess.pgn
import pytest

from pgn_player.pgn_loader import IndexingCancelled, _GameBoundaryReader, index_pgn
from pgn_player.playback import PlaybackController


def _collection(tmp_path, text):
    source = tmp_path / "recovery.pgn"
    source.write_text(text, encoding="utf-8")
    original = source.read_bytes()
    collection = index_pgn(source)
    assert source.read_bytes() == original
    return source, original, collection


def _moves(game):
    return [move.uci() for move in game.mainline_moves()]


def _tree(node):
    return (
        node.move, node.comment, node.starting_comment, frozenset(node.nags),
        tuple(_tree(child) for child in node.variations),
    )


@pytest.mark.parametrize("separator", ["\n", "\n\n", "\n \t\n\n"])
def test_blank_lines_inside_unfinished_mainline_preserve_annotations(tmp_path, separator):
    mainline = (
        "1. e4 $1 {Center.}" + separator
        + "e5 (1... c5 $5 {Sicilian.} 2. Nf3)" + separator
        + "2. Nf3 Nc6 *\n"
    )
    headers = '[Event "Whitespace"]\n[Result "*"]\n\n'
    following = '\n[Event "Following"]\n\n1. d4 d5 *\n'
    source, original, collection = _collection(tmp_path, headers + mainline + following)
    expected = chess.pgn.read_game(io.StringIO(headers + mainline.replace(separator, "\n")))
    assert expected is not None and not expected.errors
    assert len(collection) == 2
    first = collection.load_game(0)
    assert not first.errors
    assert _tree(first) == _tree(expected)
    assert _moves(first) == ["e2e4", "e7e5", "g1f3", "b8c6"]
    assert first.next().comment == "Center."
    assert first.next().nags == {chess.pgn.NAG_GOOD_MOVE}
    assert first.next().variations[1].comment == "Sicilian."
    assert collection.load_game(1).headers["Event"] == "Following"
    assert _moves(collection.load_game(1)) == ["d2d4", "d7d5"]
    assert source.read_bytes() == original


@pytest.mark.parametrize("result", ["1-0", "0-1", "1/2-1/2", "*"])
@pytest.mark.parametrize("black_move", ["", "e5\n\n"], ids=["white-ended", "black-ended"])
def test_blank_before_termination_marker_does_not_invent_an_empty_game(tmp_path, result, black_move):
    source, original, collection = _collection(
        tmp_path,
        f'[Event "First"]\n[Result "{result}"]\n\n1. e4\n\n{black_move}{result}\n\n'
        '[Event "Second"]\n\n1. d4 *\n',
    )
    assert len(collection) == 2
    first, second = collection.load_game(0), collection.load_game(1)
    assert not first.errors and not second.errors
    assert first.headers["Result"] == result
    assert _moves(first) == (["e2e4", "e7e5"] if black_move else ["e2e4"])
    assert second.headers["Event"] == "Second"
    assert _moves(second) == ["d2d4"]
    assert source.read_bytes() == original


@pytest.mark.parametrize("blank", ["\n", "\n\n"], ids=["adjacent-header", "blank-separated"])
@pytest.mark.parametrize("broken", ["1. e4 (1. d4 d5", "1. e4 (1. d4 d5 (1... Nf6"])
def test_unclosed_variation_cannot_hide_following_valid_games(tmp_path, blank, broken):
    source, original, collection = _collection(
        tmp_path,
        '[Event "Broken"]\n\n' + broken + blank
        + '[Event "Following"]\n\n1. d4 d5 *\n\n'
        + '[Event "Last"]\n\n1. Nf3 d5 *\n',
    )
    assert len(collection) == 3
    assert [entry.headers["Event"] for entry in collection.entries] == ["Broken", "Following", "Last"]
    first = collection.load_game(0)
    assert _moves(first) == ["e2e4"]
    diagnostics = [*collection.warnings, *collection.errors.get(0, []), *map(str, first.errors)]
    assert any("variation" in message.lower() or "rav" in message.lower() for message in diagnostics)
    assert _moves(collection.load_game(1)) == ["d2d4", "d7d5"]
    assert _moves(collection.load_game(2)) == ["g1f3", "d7d5"]
    assert not collection.load_game(1).errors and not collection.load_game(2).errors
    player = PlaybackController()
    player.set_collection(collection)
    assert player.next_game() and player.game_index == 1
    assert player.last() and player.board.fen() == collection.load_game(1).end().board().fen()
    assert player.next_game() and player.game_index == 2
    assert player.last() and player.board.fen() == collection.load_game(2).end().board().fen()
    assert not player.next_game()
    assert source.read_bytes() == original


def test_balanced_nested_variations_with_late_closure_and_header_decoys_are_preserved(tmp_path):
    # The raw tag-like lines are already tolerated by python-chess inside RAVs.
    # A future recovery rule must not mistake them for actual game boundaries.
    first = '''[Event "Balanced"]
[Result "*"]

{Root note with fake syntax (
[Event "Comment decoy"]
)}
1. e4 $1 (1. d4
[Event "Variation decoy"]
[White "Not a game"]
d5 (1... Nf6
[Event "Nested decoy"]
{Nested note.}
) 2. c4
{Late closure note.}
) e5 2. Nf3 Nc6 *
'''
    reference = chess.pgn.read_game(io.StringIO(first))
    assert not reference.errors
    with_blanks = first.replace('d5 (1... Nf6\n', 'd5 (1... Nf6\n\n')
    with_blanks = with_blanks.replace('\n[Event "Variation decoy"]', '\n\n[Event "Variation decoy"]')
    with_blanks = with_blanks.replace('{Late closure note.}', '\n'.join('{Late closure note.}' for _ in range(30)))
    reference_with_notes = chess.pgn.read_game(io.StringIO(first.replace('{Late closure note.}', '\n'.join('{Late closure note.}' for _ in range(30)))))
    source, original, collection = _collection(
        tmp_path, with_blanks + '\n[Event "Actual next game"]\n1. c4 e5 *\n',
    )
    assert len(collection) == 2
    game = collection.load_game(0)
    assert not game.errors
    assert _tree(game) == _tree(reference_with_notes)
    assert game.comment == reference.comment
    assert _moves(game) == ["e2e4", "e7e5", "g1f3", "b8c6"]
    assert collection.load_game(1).headers["Event"] == "Actual next game"
    assert source.read_bytes() == original


def test_recovered_following_game_keeps_its_own_variations_and_annotations(tmp_path):
    following = '''[Event "Following"]
[Result "*"]

1. d4 $1 {A closing parenthesis ) is commentary.} d5
(1... Nf6 $5 {Indian defence.} 2. c4 (2. Nf3 d5))
2. c4 e6 *
'''
    expected = chess.pgn.read_game(io.StringIO(following))
    assert expected is not None and not expected.errors
    source, original, collection = _collection(
        tmp_path, '[Event "Broken"]\n1. e4 (1. d4 d5\n' + following,
    )
    assert len(collection) == 2
    game = collection.load_game(1)
    assert not game.errors
    assert game.headers == expected.headers
    assert _tree(game) == _tree(expected)
    assert source.read_bytes() == original


@pytest.mark.parametrize("separator", ["\n", "\n\n"])
def test_missing_result_before_a_header_still_keeps_games_separate(tmp_path, separator):
    source, original, collection = _collection(
        tmp_path,
        '[Event "First"]\n1. e4\n\ne5' + separator
        + '[Event "Second"]\n1. d4 d5 *\n',
    )
    assert len(collection) == 2
    assert _moves(collection.load_game(0)) == ["e2e4", "e7e5"]
    assert _moves(collection.load_game(1)) == ["d2d4", "d7d5"]
    assert not collection.load_game(0).errors and not collection.load_game(1).errors
    assert source.read_bytes() == original


def test_headerless_missing_result_with_move_number_reset_does_not_merge(tmp_path):
    source, original, collection = _collection(tmp_path, '1. e4\n\n1. d4 d5 *\n')
    assert len(collection) == 2
    assert _moves(collection.load_game(0)) == ["e2e4"]
    assert _moves(collection.load_game(1)) == ["d2d4", "d7d5"]
    assert not collection.load_game(0).errors and not collection.load_game(1).errors
    assert source.read_bytes() == original


def test_ambiguous_unnumbered_headerless_game_after_full_move_stays_separate(tmp_path):
    source, original, collection = _collection(tmp_path, '1. e4 e5\n\nNf3 Nc6 *\n')
    assert len(collection) == 2
    assert _moves(collection.load_game(0)) == ["e2e4", "e7e5"]
    assert _moves(collection.load_game(1)) == ["g1f3", "b8c6"]
    diagnostics = [*collection.warnings, *map(str, collection.load_game(0).errors)]
    assert any("ambiguous" in message.lower() or "boundary" in message.lower() for message in diagnostics)
    assert not collection.load_game(1).errors
    assert source.read_bytes() == original


def test_blank_before_standalone_variation_stays_with_its_mainline(tmp_path):
    text = '''[Event "Variation after blank"]

1. e4 e5

(1... c5 $5 {Sicilian.} 2. Nf3) 2. Nf3 Nc6 *
'''
    expected = chess.pgn.read_game(io.StringIO(text.replace('e5\n\n(', 'e5\n(')))
    assert expected is not None and not expected.errors
    source, original, collection = _collection(tmp_path, text)
    assert len(collection) == 1
    game = collection.load_game(0)
    assert not game.errors
    assert _tree(game) == _tree(expected)
    assert source.read_bytes() == original


def test_unclosed_variation_at_eof_preserves_mainline_and_reports_diagnostic(tmp_path):
    source, original, collection = _collection(tmp_path, '[Event "Unclosed EOF"]\n1. e4 (1. d4 d5\n')
    assert len(collection) == 1
    game = collection.load_game(0)
    assert _moves(game) == ["e2e4"]
    diagnostics = [*collection.warnings, *collection.errors.get(0, []), *map(str, game.errors)]
    assert any("variation" in message.lower() or "rav" in message.lower() for message in diagnostics)
    assert source.read_bytes() == original


@pytest.mark.parametrize("result", ["1-0", "0-1", "1/2-1/2", "*"])
@pytest.mark.parametrize("following_header", ["", '[Event "Following"]\n'])
def test_result_in_unclosed_variation_does_not_swallow_following_game(tmp_path, result, following_header):
    source, original, collection = _collection(
        tmp_path,
        f'1. e4 (1. d4 d5 {result}\n\n'
        + following_header + '1. Nf3 d5 *\n',
    )
    assert len(collection) == 2
    first, following = collection.load_game(0), collection.load_game(1)
    assert _moves(first) == ["e2e4"]
    assert any("Unclosed variation" in str(error) for error in first.errors)
    assert _moves(following) == ["g1f3", "d7d5"]
    assert not following.errors
    assert source.read_bytes() == original


def test_chess960_castling_after_internal_blank_uses_the_original_position(tmp_path):
    fen = "4k3/8/8/8/8/8/8/1R2K1R1 b GB - 0 1"
    text = f'''[Event "Chess960 blank"]
[Variant "Chess960"]
[SetUp "1"]
[FEN "{fen}"]

1... Kf7

2. O-O *
'''
    expected = chess.pgn.read_game(io.StringIO(text.replace('Kf7\n\n', 'Kf7\n')))
    assert expected is not None and not expected.errors
    source, original, collection = _collection(tmp_path, text)
    assert len(collection) == 1
    game = collection.load_game(0)
    assert not game.errors
    assert game.board().chess960
    assert game.board().shredder_fen() == fen
    assert _moves(game) == ["e8f7", "e1g1"]
    assert _tree(game) == _tree(expected)
    player = PlaybackController()
    player.set_collection(collection)
    assert player.next()
    assert player.board.is_castling(game.end().move)
    assert player.next()
    assert player.board.fen() == expected.end().board().fen()
    assert source.read_bytes() == original


def test_black_fen_start_and_move_numbers_survive_internal_blank_lines(tmp_path):
    fen = "4k3/8/8/8/8/8/8/4K3 b - - 0 42"
    source, original, collection = _collection(
        tmp_path,
        f'[Event "Black start"]\n[SetUp "1"]\n[FEN "{fen}"]\n\n'
        '42... Kf7\n\n43. Kf2\n\nKe6 *\n\n'
        '[Event "Standard"]\n\n1. e4 *\n',
    )
    assert len(collection) == 2
    first = collection.load_game(0)
    assert first.board().fen() == fen
    assert first.board().fullmove_number == 42 and first.board().turn == chess.BLACK
    assert _moves(first) == ["e8f7", "e1f2", "f7e6"]
    assert not first.errors
    assert collection.load_game(1).board().fen() == chess.STARTING_FEN
    assert source.read_bytes() == original


def test_comment_punctuation_and_fake_results_do_not_end_unfinished_game(tmp_path):
    text = '''[Event "Comments"]

1. e4 {A fake result * and ( unclosed-looking punctuation.

[Event "Inside comment"]
1-0 is still commentary.}

e5 $2
; [Event "Line comment"] 0-1 ( (
% [Event "Escape line"] 1/2-1/2 ( (

2. Nf3 *

[Event "Following"]

1. d4 *
'''
    source, original, collection = _collection(tmp_path, text)
    assert len(collection) == 2
    first = collection.load_game(0)
    assert _moves(first) == ["e2e4", "e7e5", "g1f3"]
    assert first.next().comment == 'A fake result * and ( unclosed-looking punctuation.\n\n[Event "Inside comment"]\n1-0 is still commentary.'
    assert first.next().next().nags == {chess.pgn.NAG_MISTAKE}
    assert not first.errors
    assert collection.load_game(1).headers["Event"] == "Following"
    assert source.read_bytes() == original


class _CountedStream(io.StringIO):
    def __init__(self, text):
        super().__init__(text)
        self.reads = 0

    def readline(self, *args):
        self.reads += 1
        return super().readline(*args)


@pytest.mark.parametrize("tail,moves", [
    ("e5 2. Nf3 *", ["e2e4", "e7e5", "g1f3"]),
    ("(1. d4 d5) e5 *", ["e2e4", "e7e5"]),
    ("*", ["e2e4"]),
])
def test_consecutive_blank_run_is_scanned_linearly(tail, moves):
    blanks = 2000
    stream = _CountedStream('[Event "Long blank run"]\n1. e4\n' + '\n' * blanks + tail + '\n')
    reader = _GameBoundaryReader(stream, 0)
    game = chess.pgn.read_game(reader)
    assert game is not None and not game.errors
    assert _moves(game) == moves
    # Two traversals plus a small position probe are sufficient. Re-peeking
    # the rest of the blank run for each blank would exceed two million reads.
    assert stream.reads < 4 * blanks


def test_long_blank_lookahead_can_be_cancelled_and_restores_stream_position():
    prefix = '[Event "Cancel blank lookahead"]\n1. e4\n\n'
    blanks = 5000
    stream = _CountedStream(prefix + '\n' * blanks + 'e5 *\n')
    checkpoints = []

    def cancel():
        checkpoints.append(stream.reads)
        raise IndexingCancelled("Cancelled synthetic lookahead")

    reader = _GameBoundaryReader(stream, 0, cancel=cancel)
    with pytest.raises(IndexingCancelled):
        chess.pgn.read_headers(reader)
    assert checkpoints
    assert stream.reads < blanks
    assert stream.tell() == len(prefix)
