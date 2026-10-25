"""Parser recovery keeps cached seeks safe without rescanning unchanged files."""

import hashlib
from pathlib import Path
import sys

import chess
import chess.pgn
import pytest

import pgn_player.index_cache as cache_module
from pgn_player.index_cache import IndexCache
from pgn_player.models import GameInfo
from pgn_player.pgn_loader import GameCollection, index_pgn, open_pgn
from pgn_player.playback import PlaybackController
from pgn_player.storage import SettingsStore


LEGACY_RUNTIME = (
    f"{sys.implementation.name}-{sys.version_info.major}.{sys.version_info.minor}"
    f";chess-{chess.__version__};text-cookie-v1;newline-universal;errors-replace;tag-boundaries-v1"
)


def legacy_fingerprint(raw, recovered=False):
    digest = hashlib.sha256(raw)
    if recovered:
        digest.update(b"\0tag-boundaries-v1")
    return digest.hexdigest()


def save_legacy(cache, collection, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(cache_module, "RUNTIME_ID", LEGACY_RUNTIME)
        assert IndexCache(cache).save(collection)


def forbidden_index(*args, **kwargs):
    raise AssertionError("Unchanged recovery indexes must reopen without scanning the PGN")


def cached_without_source_reads(source, cache, monkeypatch, **kwargs):
    original_open = Path.open

    def guarded_open(path, *args, **options):
        assert path.resolve() != source.resolve(), "Cache lookup read the PGN source"
        return original_open(path, *args, **options)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", guarded_open)
        return open_pgn(source, cache_path=cache, indexer=forbidden_index, **kwargs)


@pytest.mark.parametrize("text, salted", [
    ('[Event "First"]\n\n1. e4 e5 *\n\n[Event "Second"]\n\n1. d4 *\n', False),
    ('[Event "First"]\n1. e4 e5 *\n[Event "Second"]\n1. d4 *\n', True),
    ('[Event "First"]\n\n1. e4 (1. d4\n\nd5) e5 *\n\n[Event "Second"]\n\n1. d4 *\n', True),
], ids=["ordinary", "dense-boundaries", "balanced-rav-paragraph"])
def test_parser_revision_rebuild_preserves_unaffected_resume_identities(tmp_path, monkeypatch, text, salted):
    source, cache = tmp_path / "study.pgn", tmp_path / "index.sqlite3"
    source.write_bytes(text.encode("utf-8"))
    original = source.read_bytes()
    fingerprint = legacy_fingerprint(original, salted)
    indexed = index_pgn(source)
    assert len(indexed) == 2
    assert indexed.fingerprint == fingerprint
    save_legacy(cache, indexed, monkeypatch)
    scans = []

    def rebuild(path, **kwargs):
        scans.append(path)
        return index_pgn(path, **kwargs)

    with SettingsStore(tmp_path / "settings.sqlite3") as store:
        store.save_resume(source, fingerprint, 1, (0,), "black")
        reopened = open_pgn(source, cache_path=cache, indexer=rebuild)
        assert cache_module.RUNTIME_ID != LEGACY_RUNTIME
        assert scans == [source.resolve()] and not reopened.from_cache
        assert reopened.fingerprint == fingerprint
        resume = store.load_resume(source, reopened.fingerprint)
        assert resume is not None and resume["orientation"] == "black"
        player = PlaybackController()
        player.set_collection(reopened)
        assert player.select_game(resume["game_index"])
        assert player.go_to(resume["variation_path"])
        assert player.game.headers["Event"] == "Second" and player.node.san() == "d4"

    saved = cached_without_source_reads(source, cache, monkeypatch)
    assert saved.from_cache and saved.fingerprint == fingerprint
    assert saved.entries == reopened.entries
    current = cached_without_source_reads(source, cache, monkeypatch, current_collection=saved)
    assert current.entries is saved.entries
    assert current.fingerprint == fingerprint and current._cache == {}
    assert source.read_bytes() == original


@pytest.mark.parametrize("kind", ["internal-blank", "unclosed-rav"])
def test_changed_recovery_ordinals_rebuild_and_do_not_restore_legacy_positions(tmp_path, monkeypatch, kind):
    source, cache = tmp_path / "changed-layout.pgn", tmp_path / "index.sqlite3"
    if kind == "internal-blank":
        text = '[Event "First"]\n\n1. e4\n\ne5 *\n\n[Event "Second"]\n\n1. d4 d5 *\n'
        legacy_entries = [GameInfo(0, {"Event": "First"}, 0),
                          GameInfo(1, {}, text.index("e5 *")),
                          GameInfo(2, {"Event": "Second"}, text.index('[Event "Second"]'))]
        expected_events = ["First", "Second"]
        legacy_salt = False
    else:
        text = ('[Event "Broken"]\n\n1. e4 (1. d4 d5\n\n'
                '[Event "Following"]\n\n1. d4 d5 *\n\n'
                '[Event "Last"]\n\n1. Nf3 d5 *\n')
        legacy_entries = [GameInfo(0, {"Event": "Broken"}, 0)]
        expected_events = ["Broken", "Following", "Last"]
        legacy_salt = True
    source.write_bytes(text.encode("utf-8"))
    original = source.read_bytes()
    old_fingerprint = legacy_fingerprint(original, legacy_salt)
    stat = source.stat()
    legacy = GameCollection(source.resolve(), legacy_entries, old_fingerprint, "utf-8-sig",
                            (stat.st_size, stat.st_mtime_ns, stat.st_ino))
    save_legacy(cache, legacy, monkeypatch)
    with SettingsStore(tmp_path / "settings.sqlite3") as store:
        store.save_resume(source, old_fingerprint, min(1, len(legacy_entries) - 1), (0,), "white")
        corrected = open_pgn(source, cache_path=cache)
        assert not corrected.from_cache
        assert [entry.headers["Event"] for entry in corrected.entries] == expected_events
        assert corrected.fingerprint != old_fingerprint
        assert store.load_resume(source, corrected.fingerprint) is None
        # Ineligible old rows are preserved, never deleted as a migration shortcut.
        assert store.load_resume(source, old_fingerprint) is not None
        store.save_resume(source, corrected.fingerprint, 1, (0,), "black")
        saved = cached_without_source_reads(source, cache, monkeypatch)
        assert saved.from_cache and saved.fingerprint == corrected.fingerprint
        assert saved.entries == corrected.entries
        resume = store.load_resume(source, saved.fingerprint)
        player = PlaybackController()
        player.set_collection(saved)
        assert player.select_game(resume["game_index"])
        assert player.go_to(resume["variation_path"])
        assert player.game.headers["Event"] == expected_events[1]
        assert player.node.san() == "d4"
    assert source.read_bytes() == original


@pytest.mark.parametrize("encoding,newline", [("utf-8-sig", "\r\n"), ("cp1252", "\r"), ("utf-8", "\n")])
def test_recovered_offsets_round_trip_without_building_move_trees(tmp_path, monkeypatch, encoding, newline):
    source, cache = tmp_path / "paragraphs.pgn", tmp_path / "index.sqlite3"
    text = "\n\n".join(
        f'[Event "Study {index}"]\n[White "René"]\n\n1. e4 $1 {{Zoë}}\n\ne5 2. Nf3 *'
        for index in range(300)
    ) + "\n"
    source.write_bytes(text.replace("\n", newline).encode(encoding))
    original = source.read_bytes()

    def no_move_tree(*args, **kwargs):
        raise AssertionError("Indexing must not construct chess.pgn.Game move trees")

    with monkeypatch.context() as patch:
        patch.setattr(chess.pgn.GameBuilder, "begin_game", no_move_tree)
        first = open_pgn(source, cache_path=cache)
    assert len(first) == 300 and first._cache == {}
    saved = cached_without_source_reads(source, cache, monkeypatch)
    assert saved.from_cache and saved.entries == first.entries
    assert bool(saved.warnings) == (encoding == "cp1252")
    for index in (299, 0, 137, *range(20), 298):
        game = saved.load_game(index)
        assert game.headers["Event"] == f"Study {index}"
        assert game.headers["White"] == "René"
        assert [move.uci() for move in game.mainline_moves()] == ["e2e4", "e7e5", "g1f3"]
        assert game.next().nags == {chess.pgn.NAG_GOOD_MOVE}
        assert game.next().comment == "Zoë"
        assert not game.errors
        assert len(saved._cache) <= 8
    assert len(saved._cache) == 8
    assert source.read_bytes() == original


def test_many_internal_blanks_do_not_reparse_the_whole_prefix(tmp_path, monkeypatch):
    """A long annotated line should need linear, bounded position-probe work."""
    source = tmp_path / "long-study.pgn"
    plies = 1024
    board = chess.Board()
    lines = []
    moves = []
    cycle = ("g1f3", "g8f6", "f3g1", "f6g8")
    for ply in range(plies):
        move = chess.Move.from_uci(cycle[ply % 4])
        number = f"{board.fullmove_number}." if board.turn else f"{board.fullmove_number}..."
        lines.append(f"{number} {board.san(move)}")
        moves.append(move)
        board.push(move)
    source.write_text('[Event "Long study"]\n\n' + "\n\n".join(lines) + " *\n", encoding="utf-8")
    parse_calls = 0
    parse_san = chess.Board.parse_san

    def counted_parse(board, san):
        nonlocal parse_calls
        parse_calls += 1
        return parse_san(board, san)

    with monkeypatch.context() as patch:
        patch.setattr(chess.Board, "parse_san", counted_parse)
        collection = index_pgn(source)
    assert len(collection) == 1 and collection._cache == {}
    # Replaying the entire prefix at each blank would make this quadratic.
    assert parse_calls <= 3 * plies
    game = collection.load_game(0)
    assert list(game.mainline_moves()) == moves
    assert not game.errors
