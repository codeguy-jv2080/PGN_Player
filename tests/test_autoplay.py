from pathlib import Path

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
