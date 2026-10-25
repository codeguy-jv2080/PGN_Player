"""Synthetic PGNs whose next header follows movetext without a blank line."""

import hashlib
import io
import sys

import chess
import chess.pgn
import pytest

import pgn_player.index_cache as cache_module
from pgn_player.index_cache import IndexCache
from pgn_player.pgn_loader import index_pgn, open_pgn


def _moves(game):
    return [move.uci() for move in game.mainline_moves()]


def _no_index(*args, **kwargs):
    raise AssertionError("An unchanged saved index must not scan the PGN again")


def test_adjacent_games_preserve_all_results_and_match_well_formed_games(tmp_path):
    records = []
    results = ("1-0", "0-1", "1/2-1/2", "*")
    lines = ("1. e4 e5", "1. d4 d5", "1. c4 e5", "1. Nf3 d5")
    for index, (result, moves) in enumerate(zip(results, lines)):
        # A boundary can start with any complete tag pair, not just Event.
        headers = f'[StudyId "{index}"]\n[Event "Study {index}"]\n[Result "{result}"]'
        records.append((headers, f"{moves} {result}"))
    dense = tmp_path / "adjacent.pgn"
    regular = tmp_path / "paragraphs.pgn"
    dense.write_text("\n".join(f"{tags}\n{moves}" for tags, moves in records) + "\n", encoding="utf-8")
    regular.write_text("\n\n".join(f"{tags}\n\n{moves}" for tags, moves in records) + "\n", encoding="utf-8")
    original = dense.read_bytes()

    collection = index_pgn(dense)
    reference = index_pgn(regular)
    assert len(collection) == len(reference) == 4
    assert reference.fingerprint == hashlib.sha256(regular.read_bytes()).hexdigest()
    assert [entry.headers["StudyId"] for entry in collection.entries] == ["0", "1", "2", "3"]
    for index, result in enumerate(results):
        game = collection.load_game(index)
        assert game.headers["Result"] == result
        assert game.headers["Event"] == f"Study {index}"
        assert len(_moves(game)) == 2
        assert str(game) == str(reference.load_game(index))
        assert not game.errors
    assert dense.read_bytes() == original


def test_next_tag_recovers_game_without_a_termination_marker(tmp_path):
    source = tmp_path / "missing-result.pgn"
    source.write_text(
        '[White "First"]\n1. e4 e5\n'
        '[Opening "Second study"]\n[White "Second"]\n1. d4 d5 *\n',
        encoding="utf-8",
    )
    collection = index_pgn(source)
    assert len(collection) == 2
    assert _moves(collection.load_game(0)) == ["e2e4", "e7e5"]
    assert _moves(collection.load_game(1)) == ["d2d4", "d7d5"]
    assert collection.entries[1].headers["Opening"] == "Second study"
    assert not collection.load_game(0).errors
    assert not collection.load_game(1).errors


def test_adjacent_fen_game_keeps_black_start_and_next_game(tmp_path):
    source = tmp_path / "fen-boundary.pgn"
    fen = "4k3/8/8/8/8/8/8/4K3 b - - 0 42"
    source.write_text(
        '[Event "Before"]\n1. e4 *\n'
        f'[FEN "{fen}"]\n[SetUp "1"]\n[Event "Black start"]\n'
        '42... Kf7 43. Kf2 *\n'
        '[Event "After"]\n1. Nf3 *\n',
        encoding="utf-8",
    )
    collection = index_pgn(source)
    assert len(collection) == 3
    game = collection.load_game(1)
    assert game.board().fen() == fen
    assert game.board().turn == chess.BLACK
    assert game.board().fullmove_number == 42
    assert _moves(game) == ["e8f7", "e1f2"]
    assert not game.errors
    assert _moves(collection.load_game(2)) == ["g1f3"]
    assert _moves(collection.load_game(0)) == ["e2e4"]


@pytest.mark.parametrize("rav_blank_lines", [False, True], ids=["continuous-rav", "blank-lines-in-rav"])
def test_tag_like_lines_in_comments_and_nested_variations_are_not_boundaries(tmp_path, rav_blank_lines):
    first = '''[Event "Annotated"]
{Before first move.}
1. e4 $1 {After e4.
[Event "BraceDecoy"]
Still inside the same comment.}
(1. d4
[Event "VariationDecoy"]
d5 (1... Nf6
[Event "NestedDecoy"]
{Flexible reply.}) 2. c4)
e5 $2
; [Event "LineDecoy"] ( {
% [Event "EscapeDecoy"] ( {
2. Nf3 Nc6 *
'''
    expected = chess.pgn.read_game(io.StringIO(first))
    assert not expected.errors
    if rav_blank_lines:
        first = first.replace('\n[Event "VariationDecoy"]', '\n\n[Event "VariationDecoy"]')
        first = first.replace('\n[Event "NestedDecoy"]', '\n\n[Event "NestedDecoy"]')
    source = tmp_path / "decoys.pgn"
    source.write_text(first + '[Event "Following"]\n1. c4 e5 *\n', encoding="utf-8")

    collection = index_pgn(source)
    assert len(collection) == 2
    assert [entry.headers["Event"] for entry in collection.entries] == ["Annotated", "Following"]
    game = collection.load_game(0)
    assert str(game) == str(expected)
    assert game.comment == "Before first move."
    assert '[Event "BraceDecoy"]' in game.next().comment
    assert chess.pgn.NAG_GOOD_MOVE in game.next().nags
    assert chess.pgn.NAG_MISTAKE in game.next().next().nags
    alternative = game.variations[1]
    assert alternative.san() == "d4"
    assert alternative.variations[1].san() == "Nf6"
    assert alternative.variations[1].comment == "Flexible reply."
    assert _moves(game) == ["e2e4", "e7e5", "g1f3", "b8c6"]
    assert not game.errors
    assert _moves(collection.load_game(1)) == ["c2c4", "e7e5"]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
def test_saved_adjacent_offsets_survive_encodings_newlines_and_cache_eviction(tmp_path, encoding, newline):
    source, cache = tmp_path / "adjacent.pgn", tmp_path / "index.sqlite3"
    lines = ("1. e4 e5", "1. d4 d5", "1. c4 e5", "1. Nf3 d5")
    first_moves = ("e2e4", "d2d4", "c2c4", "g1f3")
    text = "\n".join(
        f'[StudyId "{index}"]\n[White "René {index}"]\n'
        f'[Opening "Défense {index}"]\n{lines[index % 4]} {{Zoë {index}}} *'
        for index in range(11)
    ) + "\n"
    source.write_bytes(text.replace("\n", newline).encode(encoding))
    original = source.read_bytes()
    fresh = open_pgn(source, cache_path=cache)
    assert len(fresh) == 11
    saved = open_pgn(source, cache_path=cache, indexer=_no_index)
    assert saved.from_cache
    assert saved.entries == fresh.entries
    assert saved.fingerprint == fresh.fingerprint
    assert bool(saved.warnings) == (encoding == "cp1252")
    assert saved._cache == {}

    for index in [*range(10, -1, -1), 5, 1, 8, 0, 10, 3, 9, 2, 7, 4, 6]:
        game = saved.load_game(index)
        assert game.headers["StudyId"] == str(index)
        assert game.headers["White"] == f"René {index}"
        assert game.headers["Opening"] == f"Défense {index}"
        assert _moves(game)[0] == first_moves[index % 4]
        assert len(_moves(game)) == 2
        assert game.end().comment == f"Zoë {index}"
        assert not game.errors
        assert len(saved._cache) <= 8
    assert len(saved._cache) == 8
    assert source.read_bytes() == original


def test_pre_boundary_runtime_cache_is_rebuilt_before_reuse(tmp_path, monkeypatch):
    source, cache = tmp_path / "adjacent.pgn", tmp_path / "index.sqlite3"
    source.write_text(
        '[Event "First"]\n1. e4 *\n[Event "Second"]\n1. d4 *\n',
        encoding="utf-8",
    )
    legacy_runtime = (
        f"{sys.implementation.name}-{sys.version_info.major}.{sys.version_info.minor}"
        f";chess-{chess.__version__};text-cookie-v1;newline-universal;errors-replace"
    )
    assert cache_module.RUNTIME_ID != legacy_runtime
    collapsed = index_pgn(source)
    collapsed.entries = collapsed.entries[:1]
    collapsed.fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    with monkeypatch.context() as patch:
        patch.setattr(cache_module, "RUNTIME_ID", legacy_runtime)
        assert IndexCache(cache).save(collapsed)

    scans = []

    def rebuild(path, **kwargs):
        scans.append(path)
        return index_pgn(path, **kwargs)

    rebuilt = open_pgn(source, cache_path=cache, indexer=rebuild)
    assert scans == [source.resolve()]
    assert not rebuilt.from_cache and len(rebuilt) == 2
    assert rebuilt.fingerprint != collapsed.fingerprint
    assert _moves(rebuilt.load_game(1)) == ["d2d4"]
    saved = open_pgn(source, cache_path=cache, indexer=_no_index)
    assert saved.from_cache and saved.fingerprint == rebuilt.fingerprint


def test_blank_line_inside_variation_changes_resume_identity(tmp_path):
    source, cache = tmp_path / "variation-paragraph.pgn", tmp_path / "index.sqlite3"
    text = (
        '[Event "First"]\n\n1. e4 (1. d4\n\nd5) e5 *\n\n'
        '[Event "Second"]\n\n1. Nf3 *\n'
    )
    source.write_text(text, encoding="utf-8")
    # The previous parser indexed the continuation as a separate game. The
    # corrected second game must not inherit that continuation's saved ordinal.
    old_stream = io.StringIO(text)
    old_count = 0
    while chess.pgn.read_headers(old_stream) is not None:
        old_count += 1
    assert old_count == 3

    corrected = open_pgn(source, cache_path=cache)
    assert len(corrected) == 2
    assert corrected.fingerprint != hashlib.sha256(source.read_bytes()).hexdigest()
    first = corrected.load_game(0)
    assert _moves(first) == ["e2e4", "e7e5"]
    assert first.variations[1].san() == "d4"
    assert first.variations[1].next().san() == "d5"
    assert not first.errors
    saved = open_pgn(source, cache_path=cache, indexer=_no_index)
    assert saved.from_cache and saved.fingerprint == corrected.fingerprint
    assert saved.load_game(1).headers["Event"] == "Second"
    assert _moves(saved.load_game(1)) == ["g1f3"]


def test_malformed_initial_tag_keeps_following_headers_in_same_game(tmp_path):
    source = tmp_path / "malformed-header.pgn"
    source.write_text(
        '[Event "Study"]\n[Broken ( { ; tag]\n[White "A"]\n1. e4 e5 *\n',
        encoding="utf-8",
    )
    collection = index_pgn(source)
    assert len(collection) == 1
    assert collection.entries[0].headers["Event"] == "Study"
    assert collection.entries[0].headers["White"] == "A"
    game = collection.load_game(0)
    assert game.headers["White"] == "A"
    assert _moves(game) == ["e2e4", "e7e5"]
    assert not game.errors


@pytest.mark.parametrize("indent", ["", " \t"], ids=["plain-tags", "indented-tags"])
def test_quoted_punctuation_in_headers_does_not_change_boundaries(tmp_path, indent):
    source = tmp_path / "quoted-tags.pgn"
    source.write_text(
        f'{indent}[Event "First ({{;}})"]\n{indent}[White "A"]\n1. e4 e5 *\n'
        f'{indent}[Opening "Second ({{;}})"]\n{indent}[White "B"]\n1. d4 d5 *\n',
        encoding="utf-8",
    )
    collection = index_pgn(source)
    assert len(collection) == 2
    assert collection.entries[0].headers["Event"] == "First ({;})"
    assert collection.entries[1].headers["Opening"] == "Second ({;})"
    assert _moves(collection.load_game(0)) == ["e2e4", "e7e5"]
    assert _moves(collection.load_game(1)) == ["d2d4", "d7d5"]
    assert not collection.load_game(0).errors
    assert not collection.load_game(1).errors
