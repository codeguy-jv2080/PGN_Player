"""Read-only PGN indexing and bounded, lazy game loading.

Indexing deliberately reads headers without constructing every move tree. A full
content digest identifies a collection for resume storage; metadata and bounded
content checks guard subsequent seeks against a file edited while it is open.
"""

from __future__ import annotations

import codecs
import hashlib
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Callable

import chess.pgn

from .models import GameInfo
from .index_cache import CacheCancelled, IndexCache, PGN_BOUNDARY_ID, PGN_RECOVERY_ID
from .pgn_recovery import VariationRecovery
from .source_validation import source_sample


class PgnLoadError(ValueError):
    """A readable game or collection could not be obtained."""


class FileChangedError(PgnLoadError):
    """The source changed and must be indexed again before seeking into it."""


class GameParseError(PgnLoadError):
    """One indexed game's contents cannot produce a usable starting board."""


class IndexingCancelled(PgnLoadError):
    """The caller cancelled an indexing operation."""


class CachedIndexError(PgnLoadError):
    """A saved seek position is invalid; rebuild in the indexing worker."""


def _signature(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


_STRUCTURE = re.compile(r"[{}();]|1/2-1/2|1-0|0-1|\*")
_RESULTS = {"1-0", "0-1", "1/2-1/2", "*"}
_PROBE_TOKEN = re.compile(
    r"(?P<number>[0-9]+)\s*(?P<dots>\.+)|" + chess.pgn.MOVETEXT_REGEX.pattern,
    chess.pgn.MOVETEXT_REGEX.flags,
)


class _PositionBuilder(chess.pgn.BoardBuilder):
    """A quiet, bounded-history position probe, never a Game/move tree."""

    def begin_game(self):
        super().begin_game()
        self.failed = False

    def begin_parse_san(self, board, san):
        # BoardBuilder skips RAVs; only the most recent move is needed to
        # recognize the next RAV. Keep no history of the preceding main line.
        board.clear_stack()

    def handle_error(self, error):
        self.failed = True

    def result(self):
        return None if self.failed else super().result().copy(stack=False)


class _PositionPrefix:
    """Replay a finite segment for BoardBuilder, without storing PGN text."""

    def __init__(self, stream, lines, board, cancel=None):
        self.stream, self.lines = stream, lines
        self.cancel = cancel
        self.headers = iter(())
        if board is not None:
            variant = "Chess960" if board.chess960 else board.aliases[0]
            self.headers = iter((f'[Variant "{variant}"]\n', '[SetUp "1"]\n',
                                 f'[FEN "{board.fen()}"]\n', '\n'))

    def readline(self):
        header = next(self.headers, None)
        if header is not None:
            return header
        while self.lines:
            if self.cancel is not None and self.lines % 2048 == 0:
                self.cancel()
            self.lines -= 1
            line = self.stream.readline()
            # Whitespace in comments is irrelevant to a position-only probe.
            if not line.isspace():
                return line
        return ""


class _GameBoundaryReader:
    """Supply missing separators for one python-chess read, without editing PGN.

    Complete tag lines delimit games; malformed RAVs and unambiguous internal
    blank lines are recovered without changing source bytes. Remember an opaque
    text cookie and line count: only a recovered boundary needs a rewind/replay,
    avoiding expensive tell() calls on every line of ordinary large files.
    """

    def __init__(self, stream, start, *, recovery=None, line_base=0, cancel=None):
        self._stream = stream
        self._start = start
        self._lines = 0
        self._movetext = False
        self._in_comment = False
        self._variation_depth = 0
        self._terminated = False
        self._rav_terminated = False
        self._recovery = recovery if recovery is not None else VariationRecovery()
        self._line_base = line_base
        self._checkpoint = (start, 0, None)
        self._cancel = cancel
        self._peek_lines = 0
        self._blank_continuation_through = 0
        self.recovered_boundary = False
        self.recovered_movetext = False
        self.diagnostics = []

    def _unclosed_variation(self):
        if not self.diagnostics or "Unclosed variation" not in self.diagnostics[-1]:
            self.diagnostics.append("Unclosed variation: the recorded line ends before its closing parenthesis.")

    def _next_token(self):
        """Peek past comments, without swallowing them or changing semantics."""
        in_comment = False
        number = None
        self._peek_lines = 0
        while line := self._stream.readline():
            self._peek_lines += 1
            if self._cancel is not None and self._peek_lines % 2048 == 0:
                self._cancel()
            if not in_comment:
                if line.startswith(("%", ";")) or line.isspace():
                    continue
                if chess.pgn.TAG_REGEX.match(line.lstrip("\ufeff \t")):
                    return None, None
            position = 0
            while position < len(line):
                if in_comment:
                    closing = line.find("}", position)
                    if closing < 0:
                        break
                    in_comment, position = False, closing + 1
                match = _PROBE_TOKEN.search(line, position)
                if match is None:
                    break
                token = match.group()
                position = match.end()
                if match.group("number") is not None:
                    number = (int(match.group("number")), len(match.group("dots")) > 1)
                elif token.startswith("{"):
                    in_comment, position = True, match.start() + 1
                elif token.startswith(";"):
                    break
                elif token.startswith("$") or token in ("?", "!", "??", "!!", "!?", "?!"):
                    continue
                else:
                    return token, number
        return None, None

    def _internal_blank(self):
        after_blank = self._stream.tell()
        try:
            token, number = self._next_token()
            if token is None:
                return False
            if token in _RESULTS or token == "(":
                return True
            if token == ")":
                return False
            start, lines, board = self._checkpoint
            self._stream.seek(start)
            board = chess.pgn.read_game(
                _PositionPrefix(self._stream, self._lines - lines, board, self._cancel), Visitor=_PositionBuilder,
            )
            if board is None:
                return False
            try:
                board.parse_san(token)
            except ValueError:
                return False
            if number is not None:
                if number != (board.fullmove_number, board.turn == chess.BLACK):
                    return False
            else:
                try:
                    chess.Board().parse_san(token)
                except ValueError:
                    pass  # Legal only in the ongoing game: safe to continue.
                else:
                    self.diagnostics.append(
                        "Ambiguous blank line: kept a game boundary because the next move can also start a new game."
                    )
                    return False
            # Later blank lines replay only the new segment, avoiding quadratic
            # work in long games with a blank line after every move.
            self._checkpoint = (after_blank, self._lines, board)
            return True
        finally:
            self._stream.seek(after_blank)

    def readline(self):
        while True:
            line = self._stream.readline()
            if not line:
                if self._variation_depth:
                    self._unclosed_variation()
                return line
            self._lines += 1
            if self._cancel is not None and self._lines % 2048 == 0:
                self._cancel()
            if not self._in_comment:
                if line.startswith(("%", ";")):
                    return line
                if line.isspace():
                    # A blank line within a RAV is not a game separator.
                    if self._variation_depth:
                        if self._rav_terminated:
                            self._unclosed_variation()
                            self.recovered_movetext = True
                            return line
                        self.recovered_boundary = True
                        continue
                    if self._movetext and not self._terminated:
                        if self._lines <= self._blank_continuation_through:
                            continue
                        if self._internal_blank():
                            self.recovered_movetext = True
                            self._blank_continuation_through = self._lines + self._peek_lines - 1
                            continue
                    return line
                if not self._movetext and line.startswith("["):
                    # python-chess also ignores malformed initial tag lines;
                    # their punctuation must not enter movetext/comment state.
                    return line
                header = line.lstrip("\ufeff \t")
                if chess.pgn.TAG_REGEX.match(header):
                    broken_rav = self._variation_depth and (
                        self._rav_terminated or self._recovery.recovery_needed(
                            self._stream, self._variation_depth, self._line_base + self._lines,
                        )
                    )
                    if not self._variation_depth or broken_rav:
                        if self._movetext:
                            if broken_rav:
                                self._unclosed_variation()
                                self.recovered_movetext = True
                            self._stream.seek(self._start)
                            for _ in range(self._lines - 1):
                                self._stream.readline()
                            self._lines -= 1
                            self.recovered_boundary = True
                            return "\n"
                        return header
                    # Tag-like text inside a variation cannot start a game;
                    # punctuation in its quoted value is not RAV structure.
                    return line
            self._movetext = True
            for match in _STRUCTURE.finditer(line):
                token = match.group()
                if self._in_comment:
                    if token == "}":
                        self._in_comment = False
                elif token == ";":
                    break
                elif token == "{":
                    self._in_comment = True
                elif token == "(":
                    self._variation_depth += 1
                elif token == ")":
                    self._variation_depth = max(0, self._variation_depth - 1)
                    if not self._variation_depth:
                        self._rav_terminated = False
                elif token in _RESULTS:
                    if self._variation_depth:
                        # A result belongs to the game, not a RAV. If the RAV
                        # stays open, the next separator must end the record.
                        self._rav_terminated = True
                    else:
                        self._terminated = True
            return line


class _QuietGameBuilder(chess.pgn.GameBuilder):
    def begin_game(self):
        super().begin_game()
        self.game._source_headers = {}

    def visit_header(self, tagname, tagvalue):
        super().visit_header(tagname, tagvalue)
        self.game._source_headers[tagname] = tagvalue

    def handle_error(self, error: Exception) -> None:
        # Keep parser diagnostics attached to the game, without printing PGN
        # contents to a packaged application's log or stderr.
        self.game.errors.append(error)


class _GameDigestReader:
    """Fingerprint only the selected game as python-chess already reads it."""

    def __init__(self, reader):
        self.reader = reader
        self.digest = hashlib.sha256(b"pgn-game-text-v1\0")

    def readline(self):
        line = self.reader.readline()
        self.digest.update(line.encode("utf-8"))
        return line


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
        from_cache: bool = False,
        source_sample: str | None = None,
        end_offset: int | None = None,
    ) -> None:
        self.path = path
        self.entries = entries
        self.fingerprint = fingerprint
        self.encoding = encoding
        self.cache_size = max(1, cache_size)
        self.from_cache = from_cache
        self.source_sample = source_sample
        self.end_offset = end_offset
        self.invalid = False
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

    def _check_source(self, *, cancel=None) -> None:
        if self.invalid:
            raise FileChangedError("The PGN index is no longer valid. Reopen it to rebuild its game list.")
        try:
            unchanged = _signature(self.path) == self._signature
            if unchanged and self.source_sample is not None:
                unchanged = source_sample(self.path, self._signature[0], cancel=cancel) == self.source_sample
                unchanged = unchanged and _signature(self.path) == self._signature
        except OSError as error:
            raise PgnLoadError("The PGN file is no longer accessible. Reopen it to continue.") from error
        if not unchanged:
            self.invalid = True
            raise FileChangedError("The PGN file changed while open. Reopen it to rebuild its game list.")

    def verify_resume(self, index: int, game_digest: str) -> chess.pgn.Game:
        game = self.load_game(index)
        if game._source_digest != game_digest:
            if self.from_cache:
                self.invalid = True
                raise CachedIndexError("The saved game no longer matches the PGN. Rebuilding its index is required.")
            # A full index has already established the current file identity.
            # Reject a conflicting resume record without repeatedly rebuilding
            # the same valid index (for example, after resume data corruption).
            raise PgnLoadError("The saved game identity does not match the freshly indexed PGN.")
        return game

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
                    try:
                        stream.seek(self.entries[index].offset)
                    except (OSError, ValueError, OverflowError) as error:
                        if self.from_cache:
                            self.invalid = True
                            raise CachedIndexError("A saved game offset is invalid. Rebuilding the PGN index is required.") from error
                        raise
                    reader = _GameBoundaryReader(stream, self.entries[index].offset)
                    digest_reader = _GameDigestReader(reader)
                    game = chess.pgn.read_game(digest_reader, Visitor=_QuietGameBuilder)
                    end_offset = stream.tell()
                    if game is not None:
                        game._source_digest = digest_reader.digest.hexdigest()
                        game.errors.extend(ValueError(message) for message in reader.diagnostics)
                self._check_source()
                # Compare literal tags, before GameBuilder's default tags and
                # movetext-derived Result can alter the parsed game headers.
                if self.from_cache and (game is None or game._source_headers != self.entries[index].headers):
                    self.invalid = True
                    raise CachedIndexError("A saved game offset no longer matches its headers. Rebuilding the PGN index is required.")
                expected_end = self.entries[index + 1].offset if index + 1 < len(self.entries) else self.end_offset
                if self.from_cache and end_offset != expected_end:
                    self.invalid = True
                    raise CachedIndexError("A saved game boundary no longer matches the PGN. Rebuilding its index is required.")
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


def open_pgn(
    path: str | Path,
    *,
    cache_path: str | Path,
    progress: Callable[[int], None] | None = None,
    status: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    indexer=None,
    use_cache: bool = True,
    current_collection: GameCollection | None = None,
) -> GameCollection:
    """Reuse a saved index after metadata and fixed-size source-content checks.

    The cache lives in the caller's edition-specific data folder. Its SQLite
    connections are local to this worker; settings/resume storage is untouched.
    An unchanged currently open collection can share its header/offset list,
    avoiding a second multi-million-row allocation on a same-window reopen.
    Parsed games and errors are fresh for every returned collection. Forced
    rebuilding bypasses both current and saved indexes.
    """
    path = Path(path).expanduser().resolve()
    cache = IndexCache(cache_path)

    def check_cancel():
        if cancel is not None and cancel():
            raise IndexingCancelled("PGN loading was cancelled.")

    def phase(value):
        check_cancel()
        if status is not None:
            status(value)

    try:
        if use_cache:
            phase("cache")
            try:
                signature = _signature(path)
                if (
                    current_collection is not None
                    and os.path.normcase(str(current_collection.path)) == os.path.normcase(str(path))
                    and current_collection._signature == signature
                    and not current_collection.invalid
                    and current_collection.source_sample is not None
                    and type(current_collection.end_offset) is int and current_collection.end_offset >= 0
                ):
                    try:
                        current_collection._check_source(cancel=check_cancel)
                    except FileChangedError:
                        pass
                    else:
                        phase("current")
                        return GameCollection(path, current_collection.entries,
                            current_collection.fingerprint, current_collection.encoding,
                            signature, cache_size=current_collection.cache_size, from_cache=True,
                            source_sample=current_collection.source_sample, end_offset=current_collection.end_offset)
                saved = cache.load(path, signature, cancel=cancel)
                if (saved is not None and _signature(path) == signature
                        and source_sample(path, signature[0], cancel=check_cancel) == saved.source_sample
                        and _signature(path) == signature):
                    check_cancel()
                    return GameCollection(path, saved.entries, saved.fingerprint,
                        saved.encoding, signature, from_cache=True, source_sample=saved.source_sample,
                        end_offset=saved.end_offset)
            except (CacheCancelled, IndexingCancelled):
                raise
            except (OSError, ValueError):
                # The ordinary indexer supplies file-access errors. Broken
                # cache records and unavailable cache storage remain optional.
                pass
            finally:
                # A metadata race must not retain millions of old records while
                # the real indexer constructs their replacement.
                saved = None
        phase("indexing")
        collection = (indexer or index_pgn)(path, progress=progress, cancel=cancel)
        check_cancel()
        collection._check_source(cancel=check_cancel)
        phase("saving")
        if not cache.save(collection, cancel=cancel):
            collection.warnings.append("The PGN opened, but its index could not be saved; a later open may need to index it again.")
        check_cancel()
        collection._check_source(cancel=check_cancel)
        return collection
    except CacheCancelled as error:
        raise IndexingCancelled("PGN loading was cancelled.") from error


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
        initial_sample = source_sample(path, signature[0], cancel=check_cancel)
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
        end_offset = 0
        recovered_boundary = False
        recovered_movetext = False
        recovery = VariationRecovery(cancel=check_cancel)
        line_base = 0
        with path.open("r", encoding=encoding, errors="replace", newline=None) as stream:
            while True:
                check_cancel()
                offset = stream.tell()
                if entries:
                    # This is also the ending cookie of the last successful
                    # read_headers call. Reuse the existing tell() per game.
                    end_offset = offset
                reader = _GameBoundaryReader(stream, offset, recovery=recovery, line_base=line_base, cancel=check_cancel)
                headers = chess.pgn.read_headers(reader)
                line_base += reader._lines
                recovered_boundary |= reader.recovered_boundary
                recovered_movetext |= reader.recovered_movetext
                if headers is None:
                    break
                entries.append(GameInfo(len(entries), dict(headers), offset))
                # tell() is an opaque text cookie and may exceed the file size.
                # The underlying buffered position measures actual bytes read.
                report(30 + int(69 * min(stream.buffer.tell(), size) / size))
        if (_signature(path) != signature
                or source_sample(path, signature[0], cancel=check_cancel) != initial_sample
                or _signature(path) != signature):
            raise FileChangedError("The PGN changed while it was being indexed. Please open it again.")
        check_cancel()
        report(100)
        if recovered_boundary:
            # Newly recovered games can shift saved game numbers. Leave old
            # resume rows intact, but do not apply their ordinals to this index.
            # Ordinary files keep their original content-only fingerprint.
            digest.update(b"\0" + PGN_BOUNDARY_ID.encode("ascii"))
        if recovered_movetext:
            # The older separator adapter split/merged these records. Keep its
            # resume rows, but never restore their now-incorrect game ordinals.
            digest.update(b"\0" + PGN_RECOVERY_ID.encode("ascii"))
        return GameCollection(path, entries, digest.hexdigest(), encoding, signature,
                              source_sample=initial_sample, end_offset=end_offset)
    except (OSError, UnicodeError) as error:
        raise PgnLoadError(f"Cannot read this PGN file: {error}") from error
