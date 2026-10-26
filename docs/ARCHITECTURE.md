# Architecture

`app.py` owns startup, the explicit edition, SQLite lifetime and the Windows
running-state mutex. The main window constructs the UI and connects commands.

`pgn_loader.py` first checks the persistent index through `open_pgn()` on a
worker thread. A hit compares the normalized absolute path, size, nanosecond
modification time and inode, validates bounded source samples, then reconstructs
the existing header/offset list. A miss uses `index_pgn()` to hash source bytes
for collection identity and index headers/text seek positions. Cache hits reuse
that full-file fingerprint without hashing the whole PGN again. Source metadata
and samples are rechecked before handing back the collection.
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
rows are preserved. The separate cache and settings migrations for source and
saved-game validation are described below.
Reopening the same unchanged PGN in the current window shares its existing
header/offset list after metadata and one bounded sample check, avoiding a
second large index allocation. Parsed-game state is fresh; a forced rebuild
bypasses both caches.

## Cache validation and resume integrity

`source_validation.py` reads at most 32 KiB per probe: files up to that size
are sampled in full, and larger files use eight evenly spaced 4 KiB windows,
including the beginning and end. Reads are unbuffered and cancellable between
windows. The sample hash includes the source size and window offsets. It is
separate from the full-file collection fingerprint and the cached-record checksum.
An ordinary saved-index reopen probes before reading index rows and again
afterward, for at most 64 KiB of PGN source reads regardless of file size. It
does not scan the source or build move trees. SQLite still loads the existing
header/offset list, so that part of reopen cost scales with the number of games.

Initial indexing captures a sample before its existing source passes and checks
it afterward. A saved index must carry that original sample; saving never adds
a sample of today's bytes to an unverified older index. Cache schema 2 adds
nullable sample and terminal seek-cookie columns. Schema 1 rows are retained,
but a row missing these checks is a cache miss and is rebuilt as that particular
PGN is next opened. Other collections are not eagerly rebuilt or deleted by
this additive upgrade.

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
headers are compared before parser defaults or inferred results are applied,
and the parsed game's ending offset must match the next cached boundary or the
saved terminal cookie for the final game. The terminal cookie uses decimal TEXT
like other opaque text seek positions. Source metadata and samples are checked
before serving a parsed game and again after reading a new one.

`_GameDigestReader` hashes the selected game's decoded text as the existing
parser reads it, without another source pass or retained text copy. The digest
is attached to that parsed game, so saving its position records the identity of
the game actually displayed, even if the source has since changed. Restoration
requires the collection fingerprint, game number, saved-game digest and variation
path to agree. A game-digest mismatch on a cached collection requests a background
rebuild before any saved route is applied. If it still conflicts after fresh
indexing confirms the file fingerprint, only the resume is rejected with a notice;
this avoids a rebuild loop for damaged resume data. A missing variation path
leaves the player at that game's starting position with a notice.

Settings schema 2 adds nullable `game_digest` to resume records. Existing
settings databases are validated and backed up with SQLite's online backup API,
including committed WAL data, before the additive migration runs in a transaction.
Preferences, recent files and resume rows are preserved; a failed backup or
migration does not reset them. For a legacy resume without a game digest, a fresh
full index can prove the old collection fingerprint still matches, restore the
position and immediately save its game identity. The rare legacy row encountered
on a cache hit requests one fresh background index before restoration, covering
an interrupted upgrade without discarding a legitimate saved position. A matching
full-file fingerprint permits restoration and immediately enriches the row with
its game digest; a changed fingerprint prevents restoration. Later opens can
use the verified cached index and saved-game identity.

Bounded sampling is deliberately not complete content verification. Edits
outside the windows can escape detection when size, timestamp and file identity
are preserved. Loading a game can still expose header or boundary mismatches,
and restoring its saved position compares the game digest, but these checks do
not verify every other game. Already parsed games remain in-memory snapshots;
ordinary move navigation performs no source I/O. Reopening clears parsed-game
state. Detecting every external edit would require stronger file-system guarantees
or a full source scan, which would defeat fast multi-gigabyte cache reuse.

Selected games still load lazily into the eight-game parsed cache. PGNs are
read-only. The game list uses a Qt table model rather than constructing a widget
for every game; the unfiltered view skips unnecessary header-string searches.
Parsing warnings are retained. The index itself remains a header/offset list in
memory; no full collection of parsed move trees is created.

## Playback, presentation and packaging

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
validated defaults, recent files, collection fingerprints and saved-game digests.
Unknown, corrupt or newer databases are preserved and rejected. Migrations
require a backup and transaction. `paths.py` never crosses edition boundaries.
Frozen builds require `edition.json` next to the executable; development is
separate too.

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
