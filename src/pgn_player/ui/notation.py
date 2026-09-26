"""Safe PGN rendering and path-based move navigation."""
from html import escape
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTextBrowser
from .themes import COLORS

NAGS = {1: "!", 2: "?", 3: "!!", 4: "??", 5: "!?", 6: "?!", 10: "=", 13: "∞", 14: "⩲", 15: "⩱", 16: "±", 17: "∓", 18: "+−", 19: "−+"}


def node_path(node):
    path = []
    while node.parent is not None:
        path.append(node.parent.variations.index(node))
        node = node.parent
    return tuple(reversed(path))


class NotationWidget(QTextBrowser):
    move_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Move notation")
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._clicked)
        self.setPlaceholderText("Open a PGN to view its moves and variations.")
        self.document().setDocumentMargin(16)

    def _clicked(self, url):
        if url.scheme() == "move":
            raw = url.path()
            try:
                self.move_selected.emit(tuple(int(value) for value in raw.split(".") if value))
            except ValueError:
                pass

    def show_game(self, game, current, theme="light", training=False):
        if game is None:
            self.setHtml("<p>Open a PGN to begin.</p><p>Use the arrow keys to step through moves, or press Space to play.</p>")
            return
        colors = COLORS[theme]
        chunks = [f"<html><head><style>body{{font-family:'Segoe UI';font-size:12pt;line-height:1.65;color:{colors['text']};}} a{{color:{colors['text']};text-decoration:none;}} .comment{{color:{colors['muted']};font-size:10pt;}} .variation{{color:{colors['muted']};}} </style></head><body>"]

        def move_html(node, path, first=False):
            board = node.parent.board()
            prefix = f"{board.fullmove_number}. " if board.turn else (f"{board.fullmove_number}… " if first else "")
            san = escape(node.san())
            nags = "" if training else "".join(escape(NAGS.get(nag, f" ${nag}")) for nag in sorted(node.nags))
            selected = node is current
            variation = any(path)
            style = (' style="background:#007BFF;color:#FFFFFF;"' if selected else
                     f' style="color:{colors["muted"]};font-size:11pt;"' if variation else "")
            anchor = '<a name="current"></a>' if selected else ""
            link = ".".join(map(str, path))
            move_text = f"{san}{nags}" if variation else f"<b>{san}{nags}</b>"
            text = f'{anchor}<a href="move:{link}"{style}>{escape(prefix)}{move_text}</a>&nbsp;'
            if not training and node.comment:
                text += f'<span class="comment">{{{escape(node.comment)}}}</span>&nbsp;'
            return text

        if training:
            ancestors = []
            node = current
            while node.parent is not None:
                ancestors.append(node)
                node = node.parent
            for index, node in enumerate(reversed(ancestors)):
                chunks.append(move_html(node, node_path(node), first=index == 0))
            chunks.append('<p class="comment">Upcoming moves, comments, and variations are hidden during Guess the Move.</p>')
        else:
            if game.comment:
                chunks.append(f'<p class="comment">{escape(game.comment)}</p>')

            def line(node, path, first=True, depth=0):
                while node is not None:
                    if node.starting_comment:
                        chunks.append(f'<span class="comment">{{{escape(node.starting_comment)}}}</span> ')
                    chunks.append(move_html(node, path, first))
                    parent = node.parent
                    if path[-1] == 0:
                        for index, variation in enumerate(parent.variations[1:], 1):
                            chunks.append('<span class="variation">&nbsp;( ')
                            if depth < 60:
                                line(variation, path[:-1] + (index,), True, depth + 1)
                            else:
                                chunks.append("[deeper variation]")
                            chunks.append(' )&nbsp;</span>&nbsp;')
                    first = path[-1] == 0 and len(parent.variations) > 1
                    node = node.variations[0] if node.variations else None
                    path += (0,)

            if game.variations:
                line(game.variations[0], (0,))
            chunks.append(f'<p class="comment">{escape(game.headers.get("Result", "*"))}</p>')
        chunks.append("</body></html>")
        self.setHtml("".join(chunks))
        if current.parent is not None:
            self.scrollToAnchor("current")
