import pytest

from pgn_player.pgn_loader import IndexingCancelled, index_pgn


def test_thousands_of_games_index_lazily_and_cache_is_bounded(tmp_path):
    path = tmp_path / "generated.pgn"
    with path.open("w", encoding="utf-8") as stream:
        for number in range(2500):
            stream.write(f'[Event "Generated {number}"]\n[White "Player {number}"]\n\n1. e4 e5 *\n\n')
    progress = []
    collection = index_pgn(path, progress.append)
    assert len(collection) == 2500
    assert len(collection._cache) == 0
    assert collection.entries[2499].headers["White"] == "Player 2499"
    for index in [2499, 0, 1300, *range(20)]:
        game = collection.load_game(index)
        assert game.headers["Event"] == f"Generated {index}"
        assert len(list(game.mainline_moves())) == 2
    assert len(collection._cache) == collection.cache_size == 8
    assert progress == sorted(set(progress))
    assert progress[0] == 0 and progress[-1] == 100


def test_cancel_during_index_does_not_write_source(tmp_path):
    path = tmp_path / "cancel.pgn"
    original = ('[Event "Generated"]\n\n1. e4 *\n\n' * 500).encode()
    path.write_bytes(original)
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        return calls > 15

    with pytest.raises(IndexingCancelled):
        index_pgn(path, cancel=cancel)
    assert path.read_bytes() == original
