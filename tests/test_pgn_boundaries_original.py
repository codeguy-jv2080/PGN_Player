"""Opt-in regression against the unchanged original 2018 championship PGN.

Set PGN_PLAYER_BOUNDARY_PGN to the original file's location. The private path
and PGN itself are deliberately not stored in the repository. The reference
oracle parses each known Event-delimited game in memory, independently of the
application's indexing and seek logic; no normalization is written to disk.
"""

import hashlib
from io import StringIO
import os
from pathlib import Path
import re

import chess.pgn
import pytest

from pgn_player.autoplay import AutoplayController
from pgn_player.pgn_loader import index_pgn, open_pgn
from pgn_player.playback import PlaybackController


ORIGINAL_SHA256 = "7bc49fb442bacea340e51a2fb34ad53a9f68177ef0d525ee83d66126f813fd28"
EXPECTED_PLIES = (229, 97, 97, 67, 67, 160, 79, 75, 112, 107, 110, 62, 109, 56, 101)
EXPECTED_RESULTS = ("1/2-1/2",) * 12 + ("1-0", "0-1", "1-0")
EXPECTED_EVENTS = ("WCh 2018",) * 12 + ("WCh Rapid TB 2018",) * 3
EXPECTED_ROUNDS = tuple(str(number) for number in range(1, 13)) + ("1", "2", "3")
EXPECTED_DATES = tuple(
    f"2018.11.{day}" for day in ("09", "10", "12", "13", "15", "16", "18", "19", "21", "22", "24", "26", "28", "28", "28")
)
CARLSEN = "Carlsen, Magnus"
CARUANA = "Caruana, Fabiano"
EXPECTED_WHITE = (CARUANA, CARLSEN, CARUANA, CARLSEN, CARUANA, CARLSEN, CARLSEN, CARUANA, CARLSEN, CARUANA, CARLSEN, CARUANA, CARLSEN, CARUANA, CARLSEN)
EXPECTED_LAST_SAN = ("Ra8", "Rb4", "exf5", "Rbc1", "g4+", "Bxh6", "Kf2", "Rg5", "Ke6", "Kg3", "Bc2", "Ra8", "Kh5", "Kh7", "Qg4")


@pytest.fixture(scope="module")
def original_collection():
    location = os.environ.get("PGN_PLAYER_BOUNDARY_PGN")
    if not location:
        pytest.skip("Set PGN_PLAYER_BOUNDARY_PGN to run the original-file regression.")
    path = Path(location)
    raw = path.read_bytes()
    assert len(raw) == 15_158
    assert hashlib.sha256(raw).hexdigest() == ORIGINAL_SHA256, "The optional regression requires the unchanged original PGN."
    text = raw.decode("utf-8-sig")
    starts = [match.start() for match in re.finditer(r'(?m)^\[Event "', text)]
    assert len(starts) == 15
    # This fixture's actual defect: adjacent games have a line break but no
    # intervening blank line, including the classical-to-rapid transition.
    assert all(text[:start].endswith("\r\n") and not text[:start].endswith("\r\n\r\n") for start in starts[1:])
    references = []
    for index, (start, end) in enumerate(zip(starts, starts[1:] + [len(text)])):
        stream = StringIO(text[start:end])
        game = chess.pgn.read_game(stream)
        assert game is not None and not game.errors
        assert chess.pgn.read_game(stream) is None
        assert game.headers["Event"] == EXPECTED_EVENTS[index]
        assert game.headers["Round"] == EXPECTED_ROUNDS[index]
        assert game.headers["Date"] == EXPECTED_DATES[index]
        assert game.headers["White"] == EXPECTED_WHITE[index]
        assert game.headers["Black"] == (CARUANA if EXPECTED_WHITE[index] == CARLSEN else CARLSEN)
        assert game.headers["Result"] == EXPECTED_RESULTS[index]
        assert len(list(game.mainline_moves())) == EXPECTED_PLIES[index]
        assert game.end().san() == EXPECTED_LAST_SAN[index]
        references.append(game)
    try:
        yield path, references
    finally:
        # This also runs when a production-loader assertion fails.
        assert path.read_bytes() == raw, "The original PGN bytes must never be changed."


def test_original_indexes_all_fifteen_complete_games_and_correct_tags(original_collection, tmp_path):
    path, references = original_collection
    cache = tmp_path / "index.sqlite3"
    first = open_pgn(path, cache_path=cache)
    assert len(first) == 15 and not first.from_cache
    collection = open_pgn(path, cache_path=cache)
    assert collection.from_cache and collection.entries == first.entries
    assert collection.fingerprint == first.fingerprint
    assert len(collection) == 15
    # The content is unchanged, but the formerly collapsed game ordinals must
    # not be restored into the corrected index. Raw bytes are checked above.
    assert collection.fingerprint != ORIGINAL_SHA256
    assert tuple(entry.headers["Event"] for entry in collection.entries) == EXPECTED_EVENTS
    assert tuple(entry.headers["Round"] for entry in collection.entries) == EXPECTED_ROUNDS
    assert tuple(entry.headers["Result"] for entry in collection.entries) == EXPECTED_RESULTS
    assert len({entry.offset for entry in collection.entries}) == 15
    # Reverse reads force bounded-cache eviction and independently exercise the
    # stored seek positions rather than relying on one sequential parser pass.
    for index in (*range(15), *reversed(range(15))):
        game = collection.load_game(index)
        reference = references[index]
        assert collection.entries[index].headers == dict(reference.headers)
        assert dict(game.headers) == dict(reference.headers)
        assert not game.errors and collection.errors[index] == []
        assert list(game.mainline_moves()) == list(reference.mainline_moves())
        assert len(list(game.mainline_moves())) == EXPECTED_PLIES[index]
        assert game.end().board().fen() == reference.end().board().fen()


def test_original_manual_navigation_visits_every_ply_and_all_game_boundaries(original_collection):
    path, references = original_collection
    collection = index_pgn(path)
    assert len(collection) == 15
    player = PlaybackController()
    player.set_collection(collection)
    assert not player.previous_game()
    moves_seen = 0
    for index, reference in enumerate(references):
        if index:
            assert player.next_game()
        assert player.game_index == index and player.path == ()
        expected_board = reference.board()
        assert player.board.fen() == expected_board.fen()
        for ply, move in enumerate(reference.mainline_moves(), 1):
            assert player.next()
            expected_board.push(move)
            assert player.node.move == move
            assert player.path == (0,) * ply
            assert player.board.fen() == expected_board.fen()
            moves_seen += 1
        assert not player.next()
        assert player.game.headers["Result"] == EXPECTED_RESULTS[index]
        for remaining in reversed(range(EXPECTED_PLIES[index])):
            assert player.previous()
            expected_board.pop()
            assert player.path == (0,) * remaining
            assert player.board.fen() == expected_board.fen()
        assert not player.previous()
    assert moves_seen == 1528
    assert not player.next_game()
    for index in reversed(range(14)):
        assert player.previous_game()
        assert player.game_index == index and player.path == ()
        assert dict(player.game.headers) == dict(references[index].headers)
    assert not player.previous_game()


def test_original_autoplay_plays_every_ply_and_continues_through_rapid_games(original_collection):
    path, references = original_collection
    collection = index_pgn(path)
    assert len(collection) == 15
    player = PlaybackController()
    player.set_collection(collection)
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
        assert dict(player.game.headers) == dict(reference.headers)
        expected_board = reference.board()
        for ply, move in enumerate(reference.mainline_moves(), 1):
            assert autoplay.next_delay == autoplay.delay_seconds
            assert autoplay.step() == "move"
            expected_board.push(move)
            assert player.game_index == index and player.path == (0,) * ply
            assert player.node.move == move
            assert player.board.fen() == expected_board.fen()
            moves_seen += 1
    assert moves_seen == 1528 and transitions_seen == 14
    assert player.game_index == 14 and player.node.san() == "Qg4"
    assert not autoplay.playing
    assert autoplay.step() == "stop"
    assert not autoplay.start()


def test_original_window_lists_all_games_and_navigates_to_their_end(original_collection, qtbot, tmp_path):
    from pgn_player.paths import resolve_paths
    from pgn_player.storage import SettingsStore
    from pgn_player.ui.main_window import MainWindow

    source, references = original_collection
    paths = resolve_paths("development", app_dir=tmp_path, data_override=tmp_path / "state")
    with SettingsStore(paths.db_path) as store:
        window = MainWindow(store, paths)
        qtbot.addWidget(window)
        try:
            window.open_file(source, restore=False)
            qtbot.waitUntil(lambda: window._worker is None, timeout=10000)
            assert window.player.collection is not None, window._status_error
            assert window.game_list.model.rowCount() == 15
            for index, reference in enumerate(references):
                window._navigate(lambda index=index: window.player.select_game(index))
                assert window.player.game_index == index
                assert dict(window.player.game.headers) == dict(reference.headers)
                window._navigate(window.player.last)
                assert len(window.player.path) == EXPECTED_PLIES[index]
                assert window.player.board.fen() == reference.end().board().fen()
        finally:
            window.close()
            qtbot.waitUntil(lambda: window._worker is None, timeout=10000)
