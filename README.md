# PGN Player

A local Windows desktop player and trainer for PGN chess games. Open a file,
press Play, and watch its games continuously. Use Guess the Move to study the
recorded moves for White, Black, or both sides.

## Windows downloads

Build outputs are in `dist/`:

- **PGN Player Portable.zip** — extract the entire folder to a writable location,
  then run `PGN Player.exe`. Keep its `_internal` runtime folder and `edition.json`
  beside the executable. No Python installation is needed.
- **PGN Player Setup.exe** — per-user Windows installer, Start Menu shortcut,
  optional desktop shortcut, optional registration in Windows Open with for PGNs,
  and normal uninstall support. Windows lets you choose the default PGN app.
- **PGN Player Source.zip** — application source, tests, fixtures, build scripts,
  pinned dependency versions and license notices.

Windows 10/11, 64-bit. The application works offline. It has no account, cloud
service, telemetry, embedded browser or chess-engine download dependency.

## Using the player

1. Open a `.pgn` using File → Open, Ctrl+O, or drag a file onto the window.
2. Select a game in the Games panel. Search names, events, openings or ECO codes;
   narrow by result. The game number always refers to the original file.
3. Use arrows or playback buttons to move through the game. Click notation to
   jump directly to a mainline or variation position.
4. Press Space or Play for autoplay. Choose a move delay in the control bar.
   Automatic next-game continuation is enabled by default. Enable Loop playback
   beside it to repeat the current game, or the whole PGN when continuation is
   enabled. Preferences provide the pause between games and optional variation
   traversal.
5. Toggle Games to give more space to the board. Drag pane dividers to resize.
   Flip changes orientation, and the theme control switches light/dark mode.

The default move delay is two seconds and the between-game pause is one second.
Comments, NAGs, variations, metadata and FEN starting positions are supported.
Comments appear inline in the move list at 12 pt.
The current move is highlighted and notation follows playback. Missing optional
metadata is omitted. Malformed games display useful warnings where recovery is
possible; the original PGN is never rewritten.

The first open builds and saves a PGN index. Later opens reuse the saved offsets
and headers when the file's path, size, modification time and file identity match.
This avoids scanning or hashing an unchanged PGN again. Games still load only
when selected, with at most eight parsed games kept in memory. Loading a saved
index uses a brief status message; full indexing progress appears only when an
index must be built. Changed files and invalid indexes are rebuilt automatically.

### Guess the Move

Choose White, Black or Both. Future notation and annotations are concealed.
Click a piece and its destination, or drag it, to submit a move. Promotions offer
a piece choice. A recorded move is correct; a different legal move is incorrect,
without any engine judgment. The recorded answer is shown and Continue advances
along that line. Illegal attempts do not affect the score. In one-color modes,
the recorded opponent reply is automatic. Reset clears only session statistics.

Enable variation acceptance to accept an immediate recorded alternative and
continue along that branch. Session counts cover correct and incorrect guesses
and percentage correct; no scores or game records are written into the PGN.

Guess the Move stays active when you change games, navigate positions or open
another PGN. Your selected side and scores carry across those drills. Click
Guess the Move again or press Ctrl+G to leave training; autoplay is available
after you turn training off.

### Variations and playback

Mainline playback is the default. With Loop off, playing from a manually selected
variation follows that branch and stops at its end. Return to main line restores the
mainline at the corresponding depth. Optional all-variation playback visits
nodes in deterministic depth-first order, first child before later siblings;
the board visibly returns to the correct branch point when changing branches.
Loop playback is off by default and is saved with the other local playback
preferences. Its behavior combines with Continue to next game:

| Continue to next game | Loop playback | At the end |
| --- | --- | --- |
| Off | Off | Stop after the current game. |
| On | Off | Continue through the PGN, then stop after the final game. |
| Off | On | Return to this game's starting position and repeat it. |
| On | On | Continue through the PGN, then return to Game 1 and repeat. |

The between-game pause also applies to repeating a game and wrapping to Game 1.
The starting position is shown before the first move, using the normal move
delay. Pause stops looping; Play resumes from the displayed position. Search
filters are for finding games; continuous playback follows the original file's
order. Autoplay remains unavailable while Guess the Move is active.

## Local data and safe updates

- Portable: `data/pgn-player.sqlite3` beside that copy's executable.
- Installed: `%LOCALAPPDATA%/PGN Player/Installed/data/pgn-player.sqlite3`.
- Development: `.local-data/pgn-player.sqlite3` inside the source project.

These stores are independent. Neither edition imports from or falls back to the
other. If portable storage is unwritable, the app reports the problem rather
than using installed-edition storage. Settings and resume positions stay local.
Each edition also keeps its own `pgn-index-cache.sqlite3` in that data directory.
This disposable cache contains PGN headers, offsets and file metadata, separate
from preferences and resume positions. Cache problems fall back to normal PGN
indexing. An unreadable cache database may be preserved beside it as a `.bak`
before rebuilding; personal settings and original PGNs are never reset.
Session guess statistics last for the current session. Reopening the last file
is opt-in. Changed PGNs are reindexed and outdated resume locations are ignored.

Close the relevant edition before updating it. Replace application files in its
existing folder and keep `data/` untouched. The build script updates the same
portable folder in place and creates ZIPs from clean compiler output, never from
the working data folder. Installer updates and uninstall do not remove the
installed edition's separate data directory. Do not put the installer edition
into a portable folder; the installer rejects this.

## Source and build

See [build instructions](docs/BUILD.md), [architecture](docs/ARCHITECTURE.md), and
[keyboard shortcuts](docs/SHORTCUTS.md). Run `scripts/test.ps1` for automated tests.
All automated UI checks use offscreen rendering and temporary user data.

PGN Player is licensed under GPL-3.0-or-later. See LICENSE and
THIRD_PARTY_NOTICES.md. Chess PGN Master is the workflow reference; PGN Player is
an independent Windows implementation.
