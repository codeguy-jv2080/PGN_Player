"""Notation typography and current-position behavior, using offscreen Qt only."""

from io import StringIO

import chess
import chess.pgn
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont, QTextCursor

from pgn_player.storage import DEFAULT_NOTATION_FONT_SIZE, NOTATION_FONT_SIZES
from pgn_player.ui.notation import NotationWidget
from pgn_player.ui.themes import COLORS


ANNOTATED_PGN = '''[Event "Typography fixture"]
[Result "*"]

{Root <em>literal</em> & text.}
1. e4 $1 {Main comment <b>literal</b>.}
(1. d4 $2 {Variation comment.} d5 (1... Nf6 $5 {Nested comment.} 2. c4))
e5 2. Nf3 *
'''


def fragments(document):
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.text().strip():
                yield fragment
            iterator += 1
        block = block.next()


def assert_all_text_sizes(widget, size):
    runs = list(fragments(widget.document()))
    assert runs
    assert {run.charFormat().fontPointSize() for run in runs} == {float(size)}
    assert widget.document().defaultFont().pointSize() == size


@pytest.fixture
def notation(qtbot):
    widget = NotationWidget()
    widget.resize(520, 300)
    qtbot.addWidget(widget)
    widget.show()
    return widget


@pytest.mark.parametrize("size", NOTATION_FONT_SIZES)
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("selected_variation", [False, True], ids=["main-selected", "variation-selected"])
def test_moves_comments_nags_and_variations_share_font_size(notation, size, theme, selected_variation):
    game = chess.pgn.read_game(StringIO(ANNOTATED_PGN))
    game.variations[1].starting_comment = "Branch introduction."
    current = game.variations[1 if selected_variation else 0]
    original = str(game)
    notation.show_game(game, current, theme)
    notation.set_font_size(size)
    assert_all_text_sizes(notation, size)
    assert str(game) == original
    text = notation.toPlainText()
    for expected in (
        "e4!", "d4?", "Nf6!?", "Main comment <b>literal</b>.",
        "Variation comment.", "Nested comment.", "Branch introduction.",
        "Root <em>literal</em> & text.",
    ):
        assert expected in text
    runs = list(fragments(notation.document()))
    selected = [run for run in runs if run.charFormat().anchorHref() == ("move:1" if selected_variation else "move:0")]
    assert selected
    assert {run.charFormat().background().color().name() for run in selected} == {"#007bff"}
    assert {run.charFormat().foreground().color().name() for run in selected} == {"#ffffff"}
    assert any(run.charFormat().foreground().color().name() == COLORS[theme]["muted"].lower() for run in runs)
    assert any("e4!" in run.text() and run.charFormat().fontWeight() == QFont.Weight.Bold for run in runs)
    assert any("d4?" in run.text() and run.charFormat().fontWeight() != QFont.Weight.Bold for run in runs)


def test_font_change_reuses_context_and_keeps_move_links(notation, qtbot, monkeypatch):
    game = chess.pgn.read_game(StringIO(ANNOTATED_PGN))
    current = game.variations[1].variations[1]
    notation.show_game(game, current, "dark")
    before = (game.end().board().fen(), str(game), notation.toPlainText())

    def cannot_reload(*args, **kwargs):
        raise AssertionError("Changing notation typography must not reload a PGN.")

    monkeypatch.setattr(chess.pgn, "read_game", cannot_reload)
    for size in NOTATION_FONT_SIZES:
        notation.set_font_size(size)
        assert (game.end().board().fen(), str(game), notation.toPlainText()) == before
    with qtbot.waitSignal(notation.move_selected) as selected:
        notation.anchorClicked.emit(QUrl("move:1.1"))
    assert selected.args == [(1, 1)]


@pytest.mark.parametrize("size", NOTATION_FONT_SIZES)
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_empty_pane_uses_selected_size_and_theme(notation, size, theme):
    notation.show_game(None, None, theme)
    notation.set_font_size(size)
    assert "Open a PGN to begin." in notation.toPlainText()
    assert_all_text_sizes(notation, size)
    assert {run.charFormat().foreground().color().name() for run in fragments(notation.document())} == {COLORS[theme]["text"].lower()}


@pytest.mark.parametrize("invalid", [True, False, 12.0, "12", None, 9, 11, 20])
def test_font_size_validation_preserves_existing_view(notation, invalid):
    assert notation.font_size == DEFAULT_NOTATION_FONT_SIZE
    before = notation.toHtml()
    with pytest.raises(ValueError, match="Notation font size"):
        notation.set_font_size(invalid)
    assert notation.font_size == DEFAULT_NOTATION_FONT_SIZE
    assert notation.toHtml() == before


def test_font_size_does_not_change_widget_or_application_font(notation, qapp):
    application_font = QFont(qapp.font())
    widget_font = QFont(notation.font())
    notation.set_font_size(18)
    assert qapp.font() == application_font
    assert notation.font() == widget_font
    assert_all_text_sizes(notation, 18)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_font_changes_keep_current_move_visible_in_long_game(notation, qtbot, theme):
    game = chess.pgn.Game()
    current = game
    for ply in range(320):
        current = current.add_variation(chess.Move.from_uci(("g1f3", "g8f6", "f3g1", "f6g8")[ply % 4]))
    notation.resize(280, 180)
    notation.show_game(game, current, theme)
    for size in (18, 10, 16, 12, 14):
        notation.verticalScrollBar().setValue(0)
        notation.set_font_size(size)
        qtbot.waitUntil(lambda: notation.verticalScrollBar().value() > 0)
        selected_runs = [run for run in fragments(notation.document()) if run.charFormat().background().color().name() == "#007bff"]
        assert selected_runs
        cursor = QTextCursor(notation.document())
        cursor.setPosition(selected_runs[-1].position())
        assert notation.viewport().rect().intersects(notation.cursorRect(cursor))
        assert_all_text_sizes(notation, size)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_font_changes_preserve_training_concealment(notation, theme):
    game = chess.pgn.read_game(StringIO(ANNOTATED_PGN))
    current = game.variations[0]
    notation.show_game(game, current, theme, training=True)
    for size in NOTATION_FONT_SIZES:
        notation.set_font_size(size)
        text = notation.toPlainText()
        assert "e4" in text
        for hidden in ("e5", "Nf3", "d4", "Nf6", "!", "Main comment", "Root", "Nested comment"):
            assert hidden not in text
        assert "Upcoming moves, comments, and variations are hidden" in text
        assert_all_text_sizes(notation, size)
