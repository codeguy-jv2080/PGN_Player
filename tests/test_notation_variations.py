"""Offscreen checks for indented PGN lines, live links and training concealment."""
import io
import os
from pathlib import Path

import chess.pgn
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QTextCursor

from pgn_player.pgn_loader import index_pgn
from pgn_player.playback import PlaybackController
from pgn_player.ui.notation import NotationWidget
from pgn_player.ui.themes import COLORS


PGN = '''[Event "Indented study"]
[Result "*"]

{ROOT_COMMENT} 1. e4 $1 {MAIN_COMMENT}
({FIRST_START} 1. d4 $5 {SIDE_COMMENT} d5
    (1... Nf6 {NESTED_COMMENT} 2. c4 (2. Nf3 d5) e6)
    2. c4)
(1. c4 e5 {SECOND_SIBLING})
e5 2. Nf3 (2. Bc4 $6 Nc6) Nc6 3. Bb5 a6 *
'''


@pytest.fixture
def notation(qtbot, qapp):
    assert qapp.platformName() == "offscreen"
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font)) if font.is_file() else -1
    widget = NotationWidget()
    qtbot.addWidget(widget)
    widget.resize(600, 420)
    widget.show()
    yield widget
    if font_id >= 0:
        QFontDatabase.removeApplicationFont(font_id)


def parse(text=PGN):
    game = chess.pgn.read_game(io.StringIO(text))
    assert game is not None and not game.errors
    return game


def nodes(game):
    result = {}

    def walk(parent, path):
        for index, node in enumerate(parent.variations):
            branch = path + (index,)
            result[branch] = node
            walk(node, branch)

    walk(game, ())
    return result


def href(path):
    return "move:" + ".".join(map(str, path))


def anchor_runs(widget):
    """Join font-split fragments of one link, preserving real block geometry."""
    runs = []
    previous = None
    block = widget.document().begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                format_ = fragment.charFormat()
                link = format_.anchorHref()
                if link.startswith("move:"):
                    if previous != link:
                        runs.append({"href": link, "text": "", "start": fragment.position(),
                                     "block": block.blockNumber(), "margin": block.blockFormat().leftMargin(),
                                     "fragments": []})
                    run = runs[-1]
                    run["text"] += fragment.text()
                    run["end"] = fragment.position() + fragment.length()
                    run["fragments"].append((fragment.text(), format_))
                    previous = link
                else:
                    previous = None
            iterator += 1
        previous = None
        block = block.next()
    return runs


def export(game):
    return game.accept(chess.pgn.StringExporter(headers=True, variations=True, comments=True))


def test_nested_siblings_have_separate_indented_blocks_and_all_move_links(notation):
    game = parse()
    tree = nodes(game)
    original = export(game)
    identities = [id(node) for node in tree.values()]
    notation.set_font_size(16)
    notation.show_game(game, game)
    runs = anchor_runs(notation)
    links = {run["href"]: run for run in runs}
    assert len(runs) == len(tree)
    assert set(links) == {href(path) for path in tree}
    for path, node in tree.items():
        run = links[href(path)]
        move_fragments = [format_ for text, format_ in run["fragments"] if node.san() in text]
        assert move_fragments, f"Missing move text for {path}"
        assert all(format_.fontPointSize() == 16 for format_ in move_fragments)
        weight = QFont.Weight.Normal if any(path) else QFont.Weight.Bold
        assert all(format_.fontWeight() == weight for format_ in move_fragments)

    main = links[href((0,))]
    side = links[href((1,))]
    nested = links[href((1, 1))]
    deepest = links[href((1, 1, 1))]
    sibling = links[href((2,))]
    assert main["margin"] == 0
    assert 0 < side["margin"] < nested["margin"] < deepest["margin"]
    assert sibling["margin"] == side["margin"]
    assert len({main["block"], side["block"], nested["block"], deepest["block"], sibling["block"]}) == 5
    assert links[href((1, 0, 0))]["margin"] == side["margin"]
    assert links[href((1, 1, 0, 0))]["margin"] == nested["margin"]
    for path, number in (((0, 0), "1"), ((0, 0, 0, 0), "2")):
        resumed = links[href(path)]
        assert resumed["margin"] == 0
        assert resumed["block"] != links[href(path[:-1])]["block"]
        assert resumed["text"].startswith(number + "… ")
    for text in ("ROOT_COMMENT", "MAIN_COMMENT", "FIRST_START", "SIDE_COMMENT", "NESTED_COMMENT", "SECOND_SIBLING"):
        comment = notation.document().find(text)
        assert not comment.isNull()
        assert comment.charFormat().fontPointSize() == 16
        assert comment.charFormat().foreground().color().name() == COLORS["light"]["muted"].lower()
    assert export(game) == original
    assert [id(node) for node in nodes(game).values()] == identities


def test_mainline_without_variations_stays_in_one_compact_move_block(notation):
    game = parse('[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *\n')
    notation.show_game(game, game)
    runs = anchor_runs(notation)
    assert len(runs) == 6
    assert len({run["block"] for run in runs}) == 1
    assert all(run["margin"] == 0 for run in runs)
    assert "1. e4" in runs[0]["text"]


def test_deep_variations_keep_every_move_with_bounded_indentation(notation):
    game = chess.pgn.Game()
    game.setup(chess.Board("7k/8/8/8/8/8/8/K7 w - - 0 1"))
    node = game
    for _ in range(75):
        board = node.board()
        corner = {chess.A1, chess.A2, chess.B1, chess.B2} if board.turn else {
            chess.G7, chess.G8, chess.H7, chess.H8}
        choices = sorted((move for move in board.legal_moves if move.to_square in corner), key=lambda move: move.uci())
        assert len(choices) >= 2
        node.add_variation(choices[0])
        node = node.add_variation(choices[1])
    notation.show_game(game, game)
    runs = anchor_runs(notation)
    assert len(runs) == 150
    assert {run["href"] for run in runs} == {href(path) for path in nodes(game)}
    assert max(run["margin"] for run in runs) == 108
    assert next(run for run in runs if run["href"] == href((1,) * 75))["margin"] == 108
    assert "deeper variation" not in notation.toPlainText()


def test_fen_black_start_numbers_variation_and_resumed_mainline_correctly(notation):
    game = parse('''[SetUp "1"]
[FEN "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 12"]
[Result "*"]

12... e5 (12... c5 13. e4) 13. Nf3 Nc6 14. d4 exd4 *
''')
    notation.show_game(game, game)
    links = {run["href"]: run for run in anchor_runs(notation)}
    assert links[href((0,))]["text"].startswith("12… e5")
    assert links[href((1,))]["text"].startswith("12… c5")
    assert links[href((1,))]["margin"] > 0
    assert links[href((0, 0))]["text"].startswith("13. Nf3")
    assert links[href((0, 0))]["margin"] == 0
    assert links[href((0, 0))]["block"] != links[href((0,))]["block"]
    assert len(links) == len(nodes(game))


@pytest.mark.parametrize("theme, size", [("light", 12), ("dark", 18)])
def test_narrow_variation_link_navigates_and_current_anchor_stays_visible(notation, tmp_path, qtbot, qapp, theme, size):
    source = tmp_path / "study.pgn"
    source.write_text(PGN, encoding="utf-8")
    player = PlaybackController()
    player.set_collection(index_pgn(source))
    original = export(player.game)
    target_path = (1, 1, 1, 0)
    target_node = nodes(player.game)[target_path]
    selected = []

    def select(path):
        selected.append(path)
        assert player.go_to(path)
        notation.show_game(player.game, player.node, theme)

    notation.move_selected.connect(select)
    notation.resize(270, 160)
    notation.set_font_size(size)
    notation.show_game(player.game, player.node, theme)
    qapp.processEvents()
    run = next(run for run in anchor_runs(notation) if run["href"] == href(target_path))
    cursor = QTextCursor(notation.document())
    cursor.setPosition(run["start"] + 1)
    notation.setTextCursor(cursor)
    notation.ensureCursorVisible()
    point = notation.cursorRect(cursor).center()
    assert notation.anchorAt(point) == href(target_path)
    qtbot.mouseClick(notation.viewport(), Qt.MouseButton.LeftButton, pos=point)
    qapp.processEvents()
    assert selected == [target_path]
    assert player.path == target_path
    assert player.node is target_node
    assert player.board.fen() == target_node.board().fen()
    current = next(run for run in anchor_runs(notation) if run["href"] == href(target_path))
    cursor = QTextCursor(notation.document())
    cursor.setPosition(current["start"] + 1)
    assert notation.viewport().rect().intersects(notation.cursorRect(cursor))
    assert any(format_.background().color().name() == "#007bff" for _, format_ in current["fragments"])
    assert all(format_.fontPointSize() == size for _, format_ in current["fragments"])
    assert export(player.game) == original
    assert source.read_text(encoding="utf-8") == PGN


def test_training_shows_only_current_line_ancestors_without_comments_or_future_variations(notation):
    game = parse()
    original = export(game)
    current = nodes(game)[(1, 1)]
    notation.show_game(game, current, "dark", training=True)
    runs = anchor_runs(notation)
    assert [run["href"] for run in runs] == [href((1,)), href((1, 1))]
    text = notation.toPlainText()
    assert "d4" in text and "Nf6" in text
    for hidden in ("e4", "e5", "c4", "Nf3", "Bc4", "!?", "?!", "ROOT_COMMENT",
                   "MAIN_COMMENT", "FIRST_START", "SIDE_COMMENT", "NESTED_COMMENT", "SECOND_SIBLING"):
        assert hidden not in text
    assert export(game) == original
