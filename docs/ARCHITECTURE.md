# Architecture

`app.py` owns startup, the explicit edition, SQLite lifetime and the Windows
running-state mutex. The main window constructs the UI and connects commands.

`pgn_loader.py` first checks the persistent index through `open_pgn()` on a
worker thread. A hit compares the normalized absolute path, size, nanosecond
modification time and inode, then reconstructs the existing header/offset list.
It does not read or hash PGN contents. A miss uses `index_pgn()` to hash source
bytes for resume identity and index headers/text seek positions. The saved
fingerprint lets existing resume records work on cache hits without a storage
migration. Source metadata is checked again before handing back the collection.
Indexing and lazy game loading share a small stream adapter that supplies a
separator before a complete header tag after movetext, even when the source
omits blank lines. It tracks brace/line comments and nested variations so their
contents do not start games. Move trees, annotations, and headers are still
parsed by python-chess; the source file is never rewritten. Missing separators
use an opaque text cookie and a bounded-memory replay to preserve seek offsets.
For a tag encountered inside an unclosed variation, `pgn_recovery.py` looks for
the enclosing close before a termination marker or EOF. It ignores punctuation
in comments and quoted tags. Compact summaries of nesting changes let later
games reuse the lookahead without repeatedly scanning the same suffix. Unclosed
variations are reported through the existing per-game parsing diagnostics.
A result reached before the RAV closes also permits recovery at the next blank
separator, so a following game without headers remains accessible.

An unfinished main line can contain blank lines when the continuation is
unambiguous. The adapter uses python-chess's position-only `BoardBuilder` on
demand, with bounded move history and incremental FEN checkpoints; it never
builds game trees during indexing. Consistent move numbers or a move legal only
in the ongoing position permit continuation. A result after whitespace remains
part of that game. A new header, a move-number restart, or an ambiguous
unnumbered new-game opening preserves the separator. Ambiguity is reported
instead of silently merging games. Consecutive blanks reuse the same lookahead.

The parser revision invalidates older saved indexes once, as each file is next
opened. Ordinary files keep their content fingerprint, and files affected only
by the earlier missing-separator fix keep that fix's fingerprint. A separate
recovery fingerprint applies only when these new corrections change boundaries,
preventing old game numbers from selecting different games. Existing resume
rows are preserved; there is no settings or database-schema migration.
Reopening the same unchanged PGN in the current window shares its existing
header/offset list after those metadata checks, avoiding a second large index
allocation. Parsed-game state is fresh; a forced rebuild bypasses both caches.

`index_cache.py` owns a separate `pgn-index-cache.sqlite3` in each edition's
existing data directory. Each operation opens its own thread-local connection.
Collection metadata and records are replaced in one transaction using bounded
bulk batches, so readers see a complete old or new index. Reads use a consistent
SQLite snapshot and bounded fetch batches; only the existing GameInfo list is
retained. Slotted records and a bounded pool of repeated header names reduce
memory use for multi-million-game indexes without dropping any header tags.
All header tags remain available to search. Text seek cookies and
inodes use decimal TEXT to avoid SQLite's 64-bit integer limit. Runtime/parser
compatibility, row sequence/count, header types and a streaming checksum of
index records guard against obsolete or corrupt data. The checksum is over
cached records, not the PGN. Cancellation rolls back incomplete writes.

Cache failures fall back to normal indexing. Only disposable cache tables may
be recreated; unrelated databases and the settings database are preserved.
Whole-file cache corruption is quarantined locally when safe before rebuilding.
Invalid cached seeks discovered during individual-game loading request a new
worker rebuild rather than scanning the file on the UI thread. Literal parsed
headers are compared before parser defaults or inferred results are applied.

Selected games still load lazily into the eight-game parsed cache. PGNs are
read-only. The game list uses a Qt table model rather than constructing a widget
for every game; the unfiltered view skips unnecessary header-string searches.
Parsing warnings are retained. The index itself remains a header/offset list in
memory; no full collection of parsed move trees is created.

`playback.py` owns the collection, game and variation path. Its current node and
board are the authoritative position. Notation, metadata and board derive from
that same state. `autoplay.py` supplies pure stepping rules; the window supplies
the event-loop timer. The optional loop setting repeats the current game or wraps
the playlist to Game 1, according to next-game continuation. Each transition
returns to a game's starting position after the between-game delay, then waits
the normal move delay before playing its first move. `guess_move.py` compares
legal moves with recorded child nodes without editing the PGN tree. Opponent
replies are coordinated by the UI.

`storage.py` uses parameterized SQLite writes, a schema/application identifier,
validated defaults, recent files and resume fingerprints. Unknown, corrupt or
newer databases are preserved and rejected. Future migrations require a backup
and transaction. `paths.py` never crosses edition boundaries. Frozen builds
require `edition.json` next to the executable; development is separate too.

The UI uses Qt Widgets, QPainter and local chess SVG assets. PGN text is escaped
before rich-text rendering. Training hides future moves and their annotations.
Notation has one saved point size for moves, comments, variations and NAGs. The
View menu and guarded keyboard shortcuts update only its document and scroll to
the current move; they do not reload games or alter player/training state or
playback timers. Global widget fonts and board coordinates are unaffected.
No WebView, server or browser profile is involved.

Portable packaging copies a clean compiler payload into the established folder,
preserving its data directory. ZIP input is the clean payload mapping, never the
working portable folder. Source ZIP input is an explicit source-directory list
with runtime/cache exclusions. The installer refuses portable or unrecognized
populated target folders and does not remove user data on uninstall.
