from pathlib import Path

import chess
import pytest

from pgn_player.autoplay import AutoplayController
from pgn_player.pgn_loader import PgnLoadError, index_pgn
from pgn_player.playback import PlaybackController


FIXTURES = Path(__file__).parent / "fixtures"


def setup(name="multiple_games.pgn"):
    player = PlaybackController()
    player.set_collection(index_pgn(FIXTURES / name))
    return player, AutoplayController(player)


def test_start_pause_resume_never_skips_a_ply():
    player, autoplay = setup()
    assert autoplay.start()
    assert autoplay.step() == "move"
    assert player.node.san() == "e4"
    autoplay.pause()
    assert autoplay.step() == "stop"
    assert player.path == (0,)
    assert autoplay.toggle()
    assert autoplay.step() == "move"
    assert player.node.san() == "e5"
    assert not autoplay.toggle()


def test_continuous_playback_and_between_game_delay():
    player, autoplay = setup()
    autoplay.delay_seconds = 0.5
    autoplay.between_games_seconds = 1.5
    assert autoplay.start()
    events = []
    positions = []
    while autoplay.playing:
        events.append(autoplay.step())
        positions.append((player.game_index, player.path))
        if player.game_index == 0 and len(player.path) == 2:
            assert autoplay.next_delay == 1.5
        if player.game_index == 1 and not player.path:
            assert autoplay.next_delay == 0.5
    assert events == ["move", "move", "game", "move", "move", "move", "game", "move"]
    assert positions[-1] == (2, (0,))
    assert autoplay.step() == "stop"
    assert not autoplay.start()


def test_stop_after_game_when_continuation_disabled():
    player, autoplay = setup()
    autoplay.continue_next = False
    autoplay.start()
    autoplay.step()
    autoplay.step()
    assert player.game_index == 0
    assert not autoplay.playing


def test_depth_first_variation_playback():
    player, autoplay = setup("annotations_variations.pgn")
    autoplay.include_variations = True
    autoplay.start()
    paths = []
    while autoplay.playing:
        assert autoplay.step() == "move"
        paths.append(player.path)
    assert paths == [
        (0,), (0, 0), (0, 0, 0), (0, 0, 0, 0),
        (0, 0, 1), (0, 0, 1, 0),
        (1,), (1, 0), (1, 0, 0), (1, 1), (1, 1, 0),
    ]


def test_default_autoplay_follows_only_selected_continuation():
    player, autoplay = setup("annotations_variations.pgn")
    player.go_to((1,))
    autoplay.start()
    autoplay.step()
    autoplay.step()
    assert player.path == (1, 0, 0)
    assert not autoplay.playing


def test_selected_variation_stops_before_later_games_by_default(tmp_path):
    path = tmp_path / "variation-playlist.pgn"
    path.write_text(
        (FIXTURES / "annotations_variations.pgn").read_text()
        + "\n\n" + (FIXTURES / "multiple_games.pgn").read_text()
    )
    player = PlaybackController()
    player.set_collection(index_pgn(path))
    player.go_to((1,))
    autoplay = AutoplayController(player)
    autoplay.start()
    assert autoplay.step() == "move"
    assert autoplay.step() == "move"
    assert player.game_index == 0 and player.path == (1, 0, 0)
    assert not autoplay.playing
    assert not autoplay.start()
    autoplay.include_variations = True
    assert autoplay.start()
    assert autoplay.step() == "move"
    assert player.path == (1, 1)
    autoplay.step()
    assert autoplay.step() == "game"
    assert player.game_index == 1 and player.path == ()


def test_empty_games_continue_without_extra_moves(tmp_path):
    path = tmp_path / "empty-games.pgn"
    path.write_text('[Event "Empty"]\n\n*\n\n[Event "Next"]\n\n1. e4 *\n')
    player = PlaybackController()
    player.set_collection(index_pgn(path))
    autoplay = AutoplayController(player)
    autoplay.start()
    assert autoplay.step() == "game"
    assert player.path == ()
    assert autoplay.step() == "move"
    assert not autoplay.playing
    assert not AutoplayController(PlaybackController()).start()


def test_pausing_at_every_variation_position_neither_skips_nor_repeats():
    player, autoplay = setup("annotations_variations.pgn")
    autoplay.include_variations = True
    seen = []
    while autoplay.start():
        autoplay.step()
        seen.append((player.path, player.node.san()))
        assert player.board.fen() == player.node.board().fen()
        autoplay.pause()
        assert autoplay.step() == "stop"
        assert (player.path, player.node.san()) == seen[-1]
    assert len(seen) == 11
    assert len(set(path for path, _ in seen)) == 11
    assert seen[4] == ((0, 0, 1), "Bc4")
    assert seen[-1] == ((1, 1, 0), "c4")


def test_unreadable_next_game_stops_and_preserves_last_position(tmp_path):
    path = tmp_path / "damaged-next.pgn"
    path.write_text('[Event "Readable"]\n\n1. e4 *\n\n[SetUp "1"]\n[FEN "bad"]\n\n*\n')
    player = PlaybackController()
    player.set_collection(index_pgn(path))
    autoplay = AutoplayController(player)
    autoplay.start()
    assert autoplay.step() == "move"
    position = player.board.fen()
    with pytest.raises(PgnLoadError):
        autoplay.step()
    assert not autoplay.playing
    assert player.game_index == 0 and player.path == (0,)
    assert player.board.fen() == position


@pytest.mark.parametrize("multiple", [False, True], ids=["single-game", "playlist"])
@pytest.mark.parametrize(
    "continue_next,loop",
    [(False, False), (True, False), (False, True), (True, True)],
    ids=["once", "playlist-once", "repeat-game", "repeat-playlist"],
)
def test_loop_matrix_has_exact_moves_boundaries_and_delays(tmp_path, multiple, continue_next, loop):
    path = tmp_path / "loop.pgn"
    path.write_text(
        (FIXTURES / "multiple_games.pgn").read_text()
        if multiple else '[Event "Single"]\n\n1. e4 e5 *\n'
    )
    player = PlaybackController()
    player.set_collection(index_pgn(path))
    autoplay = AutoplayController(player)
    assert autoplay.loop is False
    autoplay.continue_next = continue_next
    autoplay.loop = loop
    autoplay.delay_seconds = 0.75
    autoplay.between_games_seconds = 1.25
    cycle = [("move", 0, (0,), "e4"), ("move", 0, (0, 0), "e5")]
    if multiple and continue_next:
        cycle += [
            ("game", 1, (), None),
            ("move", 1, (0,), "d4"),
            ("move", 1, (0, 0), "d5"),
            ("move", 1, (0, 0, 0), "c4"),
            ("game", 2, (), None),
            ("move", 2, (0,), "Nf3"),
        ]
    expected = cycle + [("game", 0, (), None)] + cycle + [("game", 0, (), None)] if loop else cycle
    assert autoplay.start()
    for event, index, position, san in expected:
        assert autoplay.next_delay == (1.25 if event == "game" else 0.75)
        assert autoplay.step() == event
        assert player.game_index == index
        assert player.path == position
        assert (player.node.san() if player.path else None) == san
        assert player.board.fen() == player.node.board().fen()
    assert autoplay.playing is loop
    if not loop:
        assert autoplay.step() == "stop"
        assert not autoplay.start()


@pytest.mark.parametrize("continue_next", [False, True], ids=["restart-current", "wrap-to-first"])
def test_loop_paused_before_and_after_final_boundary_resumes_without_moves(continue_next):
    player, autoplay = setup()
    player.select_game(2)
    autoplay.continue_next = continue_next
    autoplay.loop = True
    autoplay.delay_seconds = 0.5
    autoplay.between_games_seconds = 2.0
    assert autoplay.start()
    assert autoplay.step() == "move"
    assert player.game_index == 2 and player.path == (0,)
    assert autoplay.next_delay == 2.0
    autoplay.pause()
    assert autoplay.step() == "stop"
    assert player.game_index == 2 and player.path == (0,)
    assert autoplay.start()
    assert autoplay.next_delay == 2.0
    assert autoplay.step() == "game"
    expected_index = 0 if continue_next else 2
    assert player.game_index == expected_index and player.path == ()
    assert player.board == chess.Board()
    assert autoplay.next_delay == 0.5
    autoplay.pause()
    assert autoplay.step() == "stop"
    assert player.game_index == expected_index and player.path == ()
    assert autoplay.start()
    assert autoplay.step() == "move"
    assert player.path == (0,)
    assert player.node.san() == ("e4" if continue_next else "Nf3")


def test_repeat_current_restores_fen_start_and_black_turn():
    player, autoplay = setup("special_positions.pgn")
    player.select_game(4)
    starting_fen = player.board.fen()
    autoplay.continue_next = False
    autoplay.loop = True
    autoplay.between_games_seconds = 0
    assert autoplay.start()
    for _ in range(2):
        assert autoplay.step() == "move"
        assert player.node.san() == "Kg2"
        assert autoplay.next_delay == 0
        assert autoplay.step() == "game"
        assert player.game_index == 4 and player.path == ()
        assert player.board.fen() == starting_fen
        assert player.board.fullmove_number == 42 and player.board.turn == chess.BLACK
        assert autoplay.next_delay == autoplay.delay_seconds


@pytest.mark.parametrize("continue_next", [False, True])
def test_explicit_loop_restarts_or_advances_after_selected_variation(tmp_path, continue_next):
    path = tmp_path / "variation-loop.pgn"
    path.write_text(
        (FIXTURES / "annotations_variations.pgn").read_text()
        + '\n\n[Event "Next"]\n\n1. Nf3 *\n'
    )
    player = PlaybackController()
    player.set_collection(index_pgn(path))
    player.go_to((1,))
    autoplay = AutoplayController(player)
    autoplay.continue_next = continue_next
    autoplay.loop = True
    autoplay.start()
    assert autoplay.step() == "move" and player.path == (1, 0)
    assert autoplay.step() == "move" and player.path == (1, 0, 0)
    assert autoplay.playing
    assert autoplay.next_delay == autoplay.between_games_seconds
    assert autoplay.step() == "game"
    assert player.game_index == (1 if continue_next else 0)
    assert player.path == ()
    assert autoplay.step() == "move"
    assert player.node.san() == ("Nf3" if continue_next else "e4")


def test_loop_including_variations_repeats_complete_traversal():
    player, autoplay = setup("annotations_variations.pgn")
    autoplay.include_variations = autoplay.loop = True
    autoplay.continue_next = False
    expected = [
        (0,), (0, 0), (0, 0, 0), (0, 0, 0, 0),
        (0, 0, 1), (0, 0, 1, 0),
        (1,), (1, 0), (1, 0, 0), (1, 1), (1, 1, 0),
    ]
    autoplay.start()
    for _ in range(2):
        for path in expected:
            assert autoplay.step() == "move"
            assert player.path == path
            assert player.board.fen() == player.node.board().fen()
        assert autoplay.step() == "game"
        assert player.path == ()


@pytest.mark.parametrize("continue_next", [False, True], ids=["restart-failure", "wrap-failure"])
def test_loop_transition_source_error_stops_without_resetting_position(tmp_path, continue_next):
    path = tmp_path / "changed-loop.pgn"
    path.write_text('[Event "Original"]\n\n1. e4 *\n')
    player = PlaybackController()
    player.set_collection(index_pgn(path))
    autoplay = AutoplayController(player)
    autoplay.loop = True
    autoplay.continue_next = continue_next
    autoplay.start()
    assert autoplay.step() == "move"
    position = player.board.fen()
    path.write_text('[Event "Changed"]\n\n1. d4 d5 *\n')
    with pytest.raises(PgnLoadError):
        autoplay.step()
    assert not autoplay.playing
    assert player.game_index == 0 and player.path == (0,)
    assert player.board.fen() == position


def test_loop_without_loaded_game_cannot_start():
    autoplay = AutoplayController(PlaybackController())
    autoplay.loop = True
    assert not autoplay.start()
    assert autoplay.step() == "stop"
