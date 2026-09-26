# Architecture

`app.py` owns startup, the explicit edition, SQLite lifetime and the Windows
running-state mutex. The main window constructs the UI and connects commands.

`pgn_loader.py` indexes headers and text seek positions on a worker thread. It
hashes source bytes for resume identity and parses selected games lazily into a
bounded cache. PGNs are read-only. The game list uses a Qt table model rather
than constructing a widget for every game. Parsing warnings are retained.

`playback.py` owns the collection, game and variation path. Its current node and
board are the authoritative position. Notation, metadata and board derive from
that same state. `autoplay.py` supplies pure stepping rules; the window supplies
the event-loop timer. `guess_move.py` compares legal moves with recorded child
nodes without editing the PGN tree. Opponent replies are coordinated by the UI.

`storage.py` uses parameterized SQLite writes, a schema/application identifier,
validated defaults, recent files and resume fingerprints. Unknown, corrupt or
newer databases are preserved and rejected. Future migrations require a backup
and transaction. `paths.py` never crosses edition boundaries. Frozen builds
require `edition.json` next to the executable; development is separate too.

The UI uses Qt Widgets, QPainter and local chess SVG assets. PGN text is escaped
before rich-text rendering. Training hides future moves and their annotations.
No WebView, server or browser profile is involved.

Portable packaging copies a clean compiler payload into the established folder,
preserving its data directory. ZIP input is the clean payload mapping, never the
working portable folder. Source ZIP input is an explicit source-directory list
with runtime/cache exclusions. The installer refuses portable or unrecognized
populated target folders and does not remove user data on uninstall.
