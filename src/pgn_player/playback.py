"""The single source of truth for the selected game, move and board."""

from __future__ import annotations

import chess
import chess.pgn

from .pgn_loader import GameCollection, GameParseError, PgnLoadError


class PlaybackController:
    def __init__(self) -> None:
        self.collection: GameCollection | None = None
        self.game_index = -1
        self.game: chess.pgn.Game | None = None
        self.node: chess.pgn.GameNode | None = None
        self.path: tuple[int, ...] = ()
        self._board = chess.Board()

    @property
    def board(self) -> chess.Board:
        """Current board; callers should not mutate it."""
        return self._board

    @property
    def at_end(self) -> bool:
        return self.node is None or not self.node.variations

    def _assign_game(self, index: int, game: chess.pgn.Game) -> None:
        self.game_index = index
        self.game = game
        self.node = game
        self.path = ()
        self._board = game.board()

    def set_collection(self, collection: GameCollection) -> None:
        # Read a usable initial game before replacing the previous state. One
        # damaged FEN must not make later readable games inaccessible.
        game = None
        initial_index = -1
        skipped: list[int] = []
        for index in range(len(collection)):
            try:
                game = collection.load_game(index)
                initial_index = index
                break
            except GameParseError:
                # File-level errors deliberately propagate; only this game's
                # contents justify trying a later game in the same index.
                skipped.append(index + 1)
        if len(collection) and game is None:
            raise PgnLoadError(
                f"None of the {len(collection)} indexed games could be loaded. "
                "Check the PGN's starting-position (FEN) headers."
            )
        if skipped:
            warning = "Skipped unreadable initial game(s): " + ", ".join(map(str, skipped)) + "."
            if warning not in collection.warnings:
                collection.warnings.append(warning)
        self.collection = collection
        if game is not None:
            self._assign_game(initial_index, game)
        else:
            self.game_index = -1
            self.game = self.node = None
            self.path = ()
            self._board = chess.Board()

    def select_game(self, index: int) -> bool:
        if self.collection is None or not 0 <= index < len(self.collection):
            return False
        self._assign_game(index, self.collection.load_game(index))
        return True

    def go_to(self, path: tuple[int, ...] | list[int]) -> bool:
        if self.game is None:
            return False
        node: chess.pgn.GameNode = self.game
        board = self.game.board()
        for index in path:
            if not isinstance(index, int) or not 0 <= index < len(node.variations):
                return False
            node = node.variations[index]
            board.push(node.move)
        self.node = node
        self.path = tuple(path)
        self._board = board
        return True

    def first(self) -> bool:
        return self.go_to(())

    def previous(self) -> bool:
        return bool(self.path) and self.go_to(self.path[:-1])

    def next(self) -> bool:
        if self.node is None or not self.node.variations:
            return False
        self.node = self.node.variations[0]
        self.path += (0,)
        self._board.push(self.node.move)
        return True

    def last(self) -> bool:
        if self.node is None:
            return False
        while self.next():
            pass
        return True

    def return_mainline(self) -> bool:
        if self.game is None:
            return False
        node: chess.pgn.GameNode = self.game
        path: tuple[int, ...] = ()
        for _ in self.path:
            if not node.variations:
                break
            node = node.variations[0]
            path += (0,)
        return self.go_to(path)

    def previous_game(self) -> bool:
        return self.select_game(self.game_index - 1)

    def next_game(self) -> bool:
        return self.select_game(self.game_index + 1)
