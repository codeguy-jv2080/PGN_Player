from pathlib import Path

import chess
import pytest

from pgn_player.pgn_loader import FileChangedError, IndexingCancelled, PgnLoadError, index_pgn


FIXTURES = Path(__file__).parent / "fixtures"


def test_single_game_and_missing_optional_tags():
    collection = index_pgn(FIXTURES / "mainline.pgn")
    assert len(collection) == 1
    assert collection.entries[0].index == 0
    assert "White" in collection.entries[0].headers
    game = collection.load_game(0)
    assert len(list(game.mainline_moves())) == 10
    assert not game.errors
    assert game is collection.load_game(0)


def test_multiple_games_are_seekable():
    collection = index_pgn(FIXTURES / "multiple_games.pgn")
    assert len(collection) == 3
    assert collection.entries[2].headers["White"] == "Epsilon"
    assert collection.load_game(2).next().san() == "Nf3"
    assert collection.load_game(0).next().san() == "e4"
    with pytest.raises(IndexError):
        collection.load_game(-1)


def test_comments_nags_and_nested_variations():
    game = index_pgn(FIXTURES / "annotations_variations.pgn").load_game(0)
    assert game.comment == "Start comment."
    assert game.variations[0].comment == "Central space."
    assert chess.pgn.NAG_GOOD_MOVE in game.variations[0].nags
    assert game.variations[1].san() == "d4"
    nested = game.variations[1].variations[1]
    assert nested.san() == "Nf6"
    assert nested.comment == "Flexible reply."
    assert nested.next().san() == "c4"
    assert not game.errors


def test_fen_promotions_en_passant_mate_draw_and_black_start():
    collection = index_pgn(FIXTURES / "special_positions.pgn")
    assert len(collection) == 5
    promotion = collection.load_game(0)
    assert promotion.next().move.promotion == chess.QUEEN
    assert promotion.next().board().piece_at(chess.A8) == chess.Piece(chess.QUEEN, chess.WHITE)
    en_passant = collection.load_game(1)
    assert en_passant.board().is_en_passant(en_passant.next().move)
    assert en_passant.next().board().piece_at(chess.D5) is None
    assert collection.load_game(2).end().board().is_checkmate()
    assert collection.load_game(3).headers["Result"] == "1/2-1/2"
    assert collection.load_game(4).board().turn == chess.BLACK
    assert collection.load_game(4).board().fullmove_number == 42
    assert all(not collection.load_game(i).errors for i in range(5))


def test_castling():
    game = index_pgn(FIXTURES / "mainline.pgn").load_game(0)
    assert game.end().board().piece_at(chess.G1) == chess.Piece(chess.KING, chess.WHITE)
    assert game.end().board().piece_at(chess.G8) == chess.Piece(chess.KING, chess.BLACK)


def test_malformed_game_keeps_readable_prefix_and_next_game():
    collection = index_pgn(FIXTURES / "malformed.pgn")
    assert len(collection) == 2
    assert len(list(collection.load_game(0).mainline_moves())) == 2
    assert "illegal san" in collection.errors[0][0].lower()
    assert len(list(collection.load_game(1).mainline_moves())) == 2
    assert collection.errors[1] == []


def test_invalid_fen_is_reported_and_next_game_is_readable(tmp_path):
    path = tmp_path / "bad-fen.pgn"
    path.write_text('[SetUp "1"]\n[FEN "bad"]\n\n*\n\n[Event "Next"]\n\n1. e4 *\n')
    collection = index_pgn(path)
    with pytest.raises(PgnLoadError, match="Game 1"):
        collection.load_game(0)
    assert collection.load_game(1).next().san() == "e4"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252"])
def test_encoding_and_crlf_seeks(tmp_path, encoding):
    path = tmp_path / "encoding.pgn"
    path.write_bytes(('[White "René"]\r\n\r\n1. e4 *\r\n\r\n[White "Zoë"]\r\n\r\n1. d4 *\r\n').encode(encoding))
    original = path.read_bytes()
    collection = index_pgn(path)
    assert collection.entries[0].headers["White"] == "René"
    assert collection.load_game(1).headers["White"] == "Zoë"
    assert path.read_bytes() == original
    assert bool(collection.warnings) == (encoding == "cp1252")


def test_empty_progress_cancellation_and_missing_file(tmp_path):
    path = tmp_path / "empty.pgn"
    path.write_text("")
    updates = []
    assert len(index_pgn(path, updates.append)) == 0
    assert updates[0] == 0 and updates[-1] == 100
    assert updates == sorted(updates)
    with pytest.raises(IndexingCancelled):
        index_pgn(path, cancel=lambda: True)
    with pytest.raises(PgnLoadError):
        index_pgn(tmp_path / "missing.pgn")


def test_changed_source_does_not_return_stale_cached_game(tmp_path):
    path = tmp_path / "changed.pgn"
    path.write_text("1. e4 *")
    collection = index_pgn(path)
    collection.load_game(0)
    fingerprint = collection.fingerprint
    path.write_text("1. d4 d5 *")
    with pytest.raises(FileChangedError):
        collection.load_game(0)
    assert index_pgn(path).fingerprint != fingerprint
