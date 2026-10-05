"""Resizable chessboard with click and drag input; game state stays in the controller."""
import chess
import chess.svg
from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QSizePolicy, QWidget
from .themes import COLORS


class BoardWidget(QWidget):
    move_requested = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Chessboard")
        self.setAccessibleDescription("In Guess the Move, click a piece then its destination, or drag the piece.")
        self.setMinimumSize(260, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.board = chess.Board()
        self.orientation = "white"
        self.theme = "light"
        self.coordinates_visible = True
        self.last_move = None
        self.input_enabled = False
        self.selected = None
        self._press_square = None
        self._press_point = None
        self._drag_point = None
        self._renderers = {symbol: QSvgRenderer(QByteArray(chess.svg.piece(chess.Piece.from_symbol(symbol)).encode()), self)
                           for symbol in "PNBRQKpnbrqk"}

    def set_position(self, board, last_move=None):
        self.board = board.copy(stack=False)
        self.last_move = last_move
        self.selected = None
        self._drag_point = None
        self.update()

    def board_rect(self):
        size = max(1, min(self.width(), self.height()) - 34)
        return QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size)

    def square_rect(self, square):
        box = self.board_rect()
        file, rank = chess.square_file(square), chess.square_rank(square)
        column, row = (file, 7 - rank) if self.orientation == "white" else (7 - file, rank)
        side = box.width() / 8
        return QRectF(box.x() + column * side, box.y() + row * side, side, side)

    def square_at(self, point):
        box = self.board_rect()
        if not box.contains(point):
            return None
        column, row = int((point.x() - box.x()) * 8 / box.width()), int((point.y() - box.y()) * 8 / box.height())
        if not 0 <= column < 8 or not 0 <= row < 8:
            return None
        file, rank = (column, 7 - row) if self.orientation == "white" else (7 - column, row)
        return chess.square(file, rank)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = COLORS[self.theme]
        painter.fillRect(self.rect(), QColor(colors["background"]))
        box = self.board_rect()
        side = box.width() / 8
        for square in chess.SQUARES:
            cell = self.square_rect(square)
            dark = (chess.square_file(square) + chess.square_rank(square)) % 2 == 0
            painter.fillRect(cell, QColor(colors["dark_square" if dark else "light_square"]))
            if self.last_move and square in (self.last_move.from_square, self.last_move.to_square):
                painter.fillRect(cell, QColor(255, 215, 65, 105))
            if square == self.selected:
                painter.fillRect(cell, QColor(0, 123, 255, 100))
            piece = self.board.piece_at(square)
            if piece and not (self._drag_point is not None and square == self.selected):
                self._renderers[piece.symbol()].render(painter, cell.adjusted(side * .07, side * .07, -side * .07, -side * .07))
        if self.selected is not None and self.input_enabled:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 80, 180, 120))
            for move in self.board.legal_moves:
                if move.from_square == self.selected:
                    painter.drawEllipse(self.square_rect(move.to_square).center(), side * .085, side * .085)
        if self._drag_point is not None and self.selected is not None:
            piece = self.board.piece_at(self.selected)
            if piece:
                self._renderers[piece.symbol()].render(painter, QRectF(self._drag_point.x() - side / 2, self._drag_point.y() - side / 2, side, side))
        if self.coordinates_visible:
            painter.setPen(QPen(QColor(colors["muted"])))
            font = QFont("Segoe UI", max(8, min(12, int(side * .15))))
            painter.setFont(font)
            for index in range(8):
                file = index if self.orientation == "white" else 7 - index
                rank = 7 - index if self.orientation == "white" else index
                painter.drawText(QRectF(box.x() + index * side, box.bottom() + 1, side, 16), Qt.AlignmentFlag.AlignCenter, chess.FILE_NAMES[file])
                painter.drawText(QRectF(box.x() - 17, box.y() + index * side, 15, side), Qt.AlignmentFlag.AlignCenter, str(rank + 1))
        painter.end()

    def mousePressEvent(self, event):
        if not self.input_enabled or event.button() != Qt.MouseButton.LeftButton:
            return
        square = self.square_at(event.position())
        self._press_square = square
        self._press_point = event.position()
        if square is not None:
            piece = self.board.piece_at(square)
            if piece and piece.color == self.board.turn:
                self.selected = square
        self.update()

    def mouseMoveEvent(self, event):
        if self.input_enabled and self.selected is not None and self._press_square == self.selected and self._press_point is not None:
            if (event.position() - self._press_point).manhattanLength() > 5:
                self._drag_point = event.position()
                self.update()

    def mouseReleaseEvent(self, event):
        if not self.input_enabled or event.button() != Qt.MouseButton.LeftButton:
            return
        destination = self.square_at(event.position())
        origin = self.selected
        self._drag_point = None
        self._press_point = None
        if origin is not None and destination is not None and origin != destination:
            target = self.board.piece_at(destination)
            if not target or target.color != self.board.turn:
                self.selected = None
                self.move_requested.emit(origin, destination)
        self.update()
