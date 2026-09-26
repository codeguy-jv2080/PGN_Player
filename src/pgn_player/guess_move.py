"""Recorded-move training that never modifies a PGN's move tree."""

from __future__ import annotations

from dataclasses import dataclass

import chess

from .playback import PlaybackController


@dataclass(frozen=True)
class GuessResult:
    status: str
    expected_san: str = ""
    played_san: str = ""


class GuessMoveController:
    def __init__(self, player: PlaybackController) -> None:
        self.player = player
        self.mode = "off"
        self.include_variations = False
        self.correct = 0
        self.incorrect = 0
        self._pending: tuple[int, tuple[int, ...], str] | None = None

    @property
    def guesses(self) -> int:
        return self.correct + self.incorrect

    @property
    def percentage(self) -> float:
        return 100.0 * self.correct / self.guesses if self.guesses else 0.0

    @property
    def pending_answer(self) -> str | None:
        if self._pending is not None:
            game_id, path, answer = self._pending
            if game_id == id(self.player.game) and path == self.player.path:
                return answer
            # Manual navigation invalidates a previously shown answer.
            self._pending = None
        return None

    def _guesses_turn(self) -> bool:
        return self.mode == "both" or (
            self.mode == "white" and self.player.board.turn == chess.WHITE
        ) or (self.mode == "black" and self.player.board.turn == chess.BLACK)

    @property
    def waiting_for_guess(self) -> bool:
        return bool(
            self.mode != "off"
            and self.player.node is not None
            and self.player.node.variations
            and self._guesses_turn()
            and self.pending_answer is None
        )

    def start(self, mode: str) -> None:
        if mode not in {"off", "white", "black", "both"}:
            raise ValueError("Guess mode must be off, white, black, or both.")
        self.mode = mode
        self._pending = None
        self.reset_stats()

    def stop(self) -> None:
        self.mode = "off"
        self._pending = None

    def reset_stats(self) -> None:
        self.correct = self.incorrect = 0

    def submit(self, move: chess.Move) -> GuessResult:
        if self.mode == "off" or self.player.node is None:
            return GuessResult("inactive")
        if not self.player.node.variations:
            return GuessResult("finished")
        if self.pending_answer is not None:
            return GuessResult("pending", self.pending_answer)
        if not self._guesses_turn():
            return GuessResult("inactive")
        board = self.player.board
        if move not in board.legal_moves:
            return GuessResult("illegal")
        expected = board.san(self.player.node.variations[0].move)
        played = board.san(move)
        choices = self.player.node.variations if self.include_variations else self.player.node.variations[:1]
        for index, child in enumerate(choices):
            if move == child.move:
                self.correct += 1
                self.player.go_to(self.player.path + (index,))
                return GuessResult("correct", played, played)
        self.incorrect += 1
        self._pending = (id(self.player.game), self.player.path, expected)
        return GuessResult("incorrect", expected, played)

    def continue_recorded(self) -> bool:
        if self.pending_answer is None:
            return False
        self._pending = None
        return self.player.next()

    def play_opponent(self) -> bool:
        if self.mode in {"off", "both"} or self._guesses_turn() or self.pending_answer is not None:
            return False
        return self.player.next()
