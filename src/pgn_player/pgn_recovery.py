"""Small lexical lookahead used only for malformed variation recovery."""

from __future__ import annotations

from array import array
from bisect import bisect_left
import re
from typing import Callable

import chess.pgn


# Numeric results are whole tokens; the self-delimiting '*' may follow a move
# directly. Braces and semicolons take precedence through comment state below.
_STRUCTURE = re.compile(r"[{}();*]|(?<![\w/+-])(?:1/2-1/2|1-0|0-1)(?![\w/+-])")


class VariationRecovery:
    """Resolve a tag-like line in a RAV without buffering PGN or parsing moves.

    ``recovery_needed(stream, depth, line_number)`` is called immediately after
    reading a complete tag line outside a comment. ``line_number`` is that
    line's absolute, one-based physical line number, and ``depth`` is the open
    variation depth before it. The stream's exact text cookie is restored.

    A closing parenthesis that can close the enclosing RAV before the next
    termination token makes this a variation, not a new game. Otherwise the
    tag is a recovery boundary. Results inside comments and quoted tags do not
    terminate a probe.

    Share an instance between sequential index readers. A probe summarizes all
    candidate tags up to the next result/EOF, so later readers can consult that
    suffix at their own nesting depth without repeatedly scanning the file.
    Compact integer arrays retain only line ranges and maximum nesting drops;
    consecutive header candidates with equivalent outcomes share one range.
    Previously consumed ranges are discarded as callers advance. ``cancel``,
    if supplied, is a no-argument checkpoint that raises on cancellation.
    """

    def __init__(self, cancel: Callable[[], None] | None = None) -> None:
        self._cancel = cancel
        self._starts = array("q")
        self._ends = array("q")
        self._drops = array("q")
        self._first = 0
        self._last_query = 0

    def recovery_needed(self, stream, depth: int, line_number: int) -> bool:
        if depth <= 0:
            return False
        if line_number < 1:
            raise ValueError("Physical line numbers must be positive.")
        if self._cancel is not None:
            self._cancel()

        if line_number < self._last_query:
            # A context normally belongs to one forward pass. Clearing on a
            # backwards query is safer than reusing another pass's line base.
            self._clear()
        self._last_query = line_number
        self._first = bisect_left(self._ends, line_number, lo=self._first)
        if self._first < len(self._ends) and self._starts[self._first] <= line_number:
            drop = self._drops[self._first]
            self._prune()
            return depth > drop

        cookie = stream.tell()
        try:
            self._scan(stream, line_number)
        finally:
            stream.seek(cookie)
        return depth > self._drops[0]

    def _clear(self) -> None:
        self._starts = array("q")
        self._ends = array("q")
        self._drops = array("q")
        self._first = 0

    def _prune(self) -> None:
        # Amortized compaction avoids copying the remainder on every game.
        if self._first and self._first * 2 >= len(self._ends):
            self._starts = self._starts[self._first:]
            self._ends = self._ends[self._first:]
            self._drops = self._drops[self._first:]
            self._first = 0

    def _scan(self, stream, line_number: int) -> None:
        starts = array("q", [line_number])
        ends = array("q", [line_number])
        levels = array("q", [0])
        minima = array("q")
        level = 0
        segment_minimum = 0
        in_comment = False
        finished = False
        scanned = 0

        while not finished:
            line = stream.readline()
            if not line:
                break
            line_number += 1
            scanned += 1
            if self._cancel is not None and scanned % 2048 == 0:
                self._cancel()
            if not in_comment:
                if line.startswith(("%", ";")):
                    continue
                if chess.pgn.TAG_REGEX.match(line.lstrip("\ufeff \t")):
                    if level == levels[-1] and segment_minimum >= level:
                        # No intervening decrease: both candidates have the
                        # same future minimum, whatever the remaining file is.
                        ends[-1] = line_number
                    else:
                        minima.append(segment_minimum)
                        starts.append(line_number)
                        ends.append(line_number)
                        levels.append(level)
                    segment_minimum = level
                    continue

            for match in _STRUCTURE.finditer(line):
                token = match.group()
                if in_comment:
                    if token == "}":
                        in_comment = False
                elif token == ";":
                    break
                elif token == "{":
                    in_comment = True
                elif token == "(":
                    level += 1
                elif token == ")":
                    # Relative depth is deliberately not clamped: each cached
                    # candidate is later queried at its own starting depth.
                    level -= 1
                    segment_minimum = min(segment_minimum, level)
                elif token != "}":
                    finished = True
                    break

        minima.append(segment_minimum)
        suffix_minimum = minima[-1]
        for index in range(len(levels) - 1, -1, -1):
            suffix_minimum = min(suffix_minimum, minima[index])
            levels[index] = max(0, levels[index] - suffix_minimum)

        self._clear()
        for start, end, drop in zip(starts, ends, levels):
            if self._drops and drop == self._drops[-1]:
                self._ends[-1] = end
            else:
                self._starts.append(start)
                self._ends.append(end)
                self._drops.append(drop)
