"""Read-only PGN indexing and bounded, lazy game loading.

Indexing deliberately reads headers without constructing every move tree. A full
content digest identifies a collection for resume storage; metadata checks guard
subsequent seeks against a file edited while it is open.
"""

from __future__ import annotations

import codecs
import hashlib
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Callable

import chess.pgn

from .models import GameInfo


class PgnLoadError(ValueError):
    """A readable game or collection could not be obtained."""


class FileChangedError(PgnLoadError):
    """The source changed and must be indexed again before seeking into it."""


class GameParseError(PgnLoadError):
    """One indexed game's contents cannot produce a usable starting board."""


class IndexingCancelled(PgnLoadError):
    """The caller cancelled an indexing operation."""


def _signature(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


class _QuietGameBuilder(chess.pgn.GameBuilder):
    def handle_error(self, error: Exception) -> None:
        # Keep parser diagnostics attached to the game, without printing PGN
        # contents to a packaged application's log or stderr.
        self.game.errors.append(error)


class GameCollection:
    """An index over an unchanged PGN file, with at most eight parsed games."""

    def __init__(
        self,
        path: Path,
        entries: list[GameInfo],
        fingerprint: str,
        encoding: str,
        signature: tuple[int, int, int],
        *,
        cache_size: int = 8,
    ) -> None:
        self.path = path
        self.entries = entries
        self.fingerprint = fingerprint
        self.encoding = encoding
        self.cache_size = max(1, cache_size)
        self.errors: dict[int, list[str]] = {}
        self.warnings = (
            ["This file is not UTF-8; text is being read using Windows-1252."]
            if encoding == "cp1252" else []
        )
        self._signature = signature
        self._cache: OrderedDict[int, chess.pgn.Game] = OrderedDict()
        self._lock = threading.RLock()

    def __len__(self) -> int:
        return len(self.entries)

    def _check_source(self) -> None:
        try:
            unchanged = _signature(self.path) == self._signature
        except OSError as error:
            raise PgnLoadError("The PGN file is no longer accessible. Reopen it to continue.") from error
        if not unchanged:
            raise FileChangedError("The PGN file changed while open. Reopen it to rebuild its game list.")

    def load_game(self, index: int) -> chess.pgn.Game:
        if not 0 <= index < len(self.entries):
            raise IndexError("Game number is outside this PGN collection.")
        with self._lock:
            self._check_source()
            if index in self._cache:
                self._cache.move_to_end(index)
                return self._cache[index]
            try:
                with self.path.open("r", encoding=self.encoding, errors="replace", newline=None) as stream:
                    stream.seek(self.entries[index].offset)
                    game = chess.pgn.read_game(stream, Visitor=_QuietGameBuilder)
                self._check_source()
                if game is None:
                    message = f"Game {index + 1} could not be read."
                    self.errors[index] = [message]
                    raise GameParseError(message)
                # Invalid FEN headers must fail here, before UI state changes.
                game.board()
            except (OSError, ValueError, IndexError) as error:
                if isinstance(error, PgnLoadError):
                    raise
                message = f"Game {index + 1} could not be loaded: {error}"
                self.errors[index] = [message]
                if isinstance(error, OSError):
                    raise PgnLoadError(message) from error
                raise GameParseError(message) from error
            self.errors[index] = [str(error) for error in game.errors]
            self._cache[index] = game
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
            return game


def index_pgn(
    path: str | Path,
    progress: Callable[[int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> GameCollection:
    """Index a file without changing it; suitable for execution in a worker.

    UTF-8 (including BOM) is preferred. Legacy Windows-1252 files are supported
    with a visible collection warning. Cancellation raises IndexingCancelled.
    Empty PGNs return an empty collection rather than inventing a game.
    """
    path = Path(path).expanduser().resolve()
    last_progress = -1

    def report(value: int) -> None:
        nonlocal last_progress
        value = min(100, max(0, value))
        if progress is not None and value != last_progress:
            progress(value)
        last_progress = value

    def check_cancel() -> None:
        if cancel is not None and cancel():
            raise IndexingCancelled("PGN loading was cancelled.")

    check_cancel()
    report(0)
    try:
        signature = _signature(path)
        size = max(signature[0], 1)
        digest = hashlib.sha256()
        decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        encoding = "utf-8-sig"
        read_bytes = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                check_cancel()
                digest.update(chunk)
                read_bytes += len(chunk)
                if encoding == "utf-8-sig":
                    try:
                        decoder.decode(chunk)
                    except UnicodeDecodeError:
                        encoding = "cp1252"
                report(int(30 * read_bytes / size))
            if encoding == "utf-8-sig":
                try:
                    decoder.decode(b"", final=True)
                except UnicodeDecodeError:
                    encoding = "cp1252"

        entries: list[GameInfo] = []
        with path.open("r", encoding=encoding, errors="replace", newline=None) as stream:
            while True:
                check_cancel()
                offset = stream.tell()
                headers = chess.pgn.read_headers(stream)
                if headers is None:
                    break
                entries.append(GameInfo(len(entries), dict(headers), offset))
                report(30 + int(69 * min(stream.tell(), size) / size))
        if _signature(path) != signature:
            raise FileChangedError("The PGN changed while it was being indexed. Please open it again.")
        check_cancel()
        report(100)
        return GameCollection(path, entries, digest.hexdigest(), encoding, signature)
    except (OSError, UnicodeError) as error:
        raise PgnLoadError(f"Cannot read this PGN file: {error}") from error
