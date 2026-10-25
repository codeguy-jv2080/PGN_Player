# Chess960 compatibility audit

The audit used the unmodified `Chess960_10_Great_Games.pgn`: **10 games,
864 plies, and 14 castlings (12 kingside, 2 queenside)**. All games parsed
without errors. The source bytes remained unchanged. Dedicated synthetic
positions extend coverage beyond that collection.

## Confirmed defect and correction

Chess960 castling is represented by python-chess as a king-to-own-rook move.
The board previously selected that rook on the second click, while dropping
a dragged king onto it was rejected as a friendly target.

`src/pgn_player/ui/board.py` now allows that target only when python-chess
identifies the move as a **legal Chess960 castle**. This fixes both click
and drag input in Guess the Move. Click the king and then its castling rook,
or drag the king onto that rook. Standard chess still uses the king's ordinary
castling destination. Other friendly-piece targets remain blocked.

No parser, rules, playback, notation, export, storage, or training-controller
rewrite was needed. The earlier PGN game-boundary correction is preserved.

## Verification

| Area | Result and coverage |
| --- | --- |
| Recognition and indexing | Passed: Variant/FEN headers, KQ and rook-file castling rights, all 10 original games, cached reopening, mixed standard/Chess960 games, adjacent headers without blank separators. |
| Starting positions | Passed: each game's actual FEN, pieces, turn, Chess960 flag, and castling rights are preserved. |
| Castling | Passed for both colors and both sides: stationary king, stationary rook, exchanging king/rook squares, overlapping paths, unusual starting files, and one-square king travel. |
| Illegal castling | Passed: no acceptance when in check, crossing/landing on attacked squares, obstructed, or without rights. Legality remains python-chess's responsibility. |
| Guess the Move | Passed after the input fix: clicks/drags, both board orientations, White/Black/Both, correct/incorrect scoring, accepted variations, hidden future notation, normal king moves, and opponent replies. |
| Navigation | Passed: every original move forward/backward, game selection, first/last, wheel, arrows, and all six numeric-keypad commands with synthetic Num Lock on/off key forms. |
| Autoplay | Passed: all original games, all four Continue/Loop combinations, game/collection wrap, pause/resume, and variation traversal. |
| Resume | Passed: persisted positions before/after every original castle; saved variation paths; integrated window reopening and board orientation. |
| Display and notation | Passed: starting/final board positions, legal-target indicators, flipping, both themes, O-O/O-O-O SAN, comments, NAGs, and nested variations. |
| Move highlights | Passed: existing python-chess convention is retained; Chess960 castles highlight the encoded king and rook starting squares. |
| Sound routing | Passed: castling uses the move sound, captures use the capture sound, backward moves remain silent. Training and autoplay paths are covered with a silent audio substitute. |
| Export/reimport | Passed through the application's export command for all 10 original games; headers, exact source FEN, results, complete move trees and final positions match. Synthetic tests also preserve comments, NAGs and nested variations. |
| Standard chess | Standard castling and ordinary king moves are covered alongside the existing regression suite. |

Tests live in `tests/test_chess960_core.py`, `tests/test_chess960_ui.py`,
and `tests/test_chess960_original.py`. The optional original-file tests are
enabled as described in [BUILD.md](BUILD.md). The original collection and its
private location are excluded from source and packages.

## Limits

UI checks run offscreen with synthetic mouse/key events; they do not control
the user's desktop or physically toggle Num Lock. Sound tests verify routing
without playing audio. Exported files are reimported through PGN Player and
python-chess; this audit does not launch other chess applications. The tests
cover representative legal configurations, not an exhaustive proof of every
possible Chess960 position.
