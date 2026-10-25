# Build PGN Player on Windows

## Requirements

- Windows 10/11 x64
- CPython 3.10–3.14 x64 (the range accepted by the pinned dependencies)
- Inno Setup 6 for installer compilation
- Internet access only to obtain development dependencies

The supplied build and tests were verified with CPython 3.10.6 x64. Other
interpreter versions in the accepted range have not been verified. Each build
records its exact runtime and packaging versions in `licenses/runtime/VERSIONS.txt`.

Use PowerShell in the source folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
```

Run from source:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
.\.venv\Scripts\python.exe -m pgn_player
```

Source launches use their own `.local-data` directory. Do not copy real user
databases, logs or settings into source, fixtures or packaging directories.

## Verify and build

```powershell
.\scripts\test.ps1
.\scripts\build.ps1
```

The build script also runs all tests before packaging. It checks whether PGN
Player is running and stops with a text error if it is. It never closes a user
application or chooses another release folder. A locked file also stops the
update. Close the app yourself, then repeat the same command.

If Inno Setup is outside its standard locations:

```powershell
.\scripts\build.ps1 -InnoCompiler 'D:\Tools\Inno Setup 6\ISCC.exe'
```

The script generates the icon from the original SVG, collects license notices,
freezes an edition-neutral one-folder application, runs an installed-edition
payload smoke test, updates the canonical portable folder, writes clean
archives, compiles the installer, and runs the portable executable offscreen.
All smoke tests use temporary databases. The installer is compiled, not installed
onto the developer's Windows profile by the test procedure.

The synthetic tests cover game boundaries without blank-line separators,
unclosed variation recovery, and unambiguous blank lines inside movetext:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_pgn_recovery.py tests/test_pgn_recovery_cache.py tests/test_pgn_recovery_lookahead.py --basetemp=.build/recovery-tests
```

These include nested variations, comment/tag decoys, annotations, all result
markers, missing results, FEN and Chess960 starts, cancellation, unchanged source
bytes, encoded seek offsets, cache/resume migration, and linear-work checks for
long malformed collections and repeated blank lines. Index tests forbid game
tree construction, and unchanged-cache tests forbid PGN source reads. Ambiguous
unnumbered continuations retain a boundary and a diagnostic; the parser does
not guess whether two plausible headerless games were intended to be one.

To also run the regression against the original, unmodified `WorldChamp2018.pgn`,
set its location for the test process (replace the example path):

```powershell
$env:PGN_PLAYER_BOUNDARY_PGN = 'C:\PGNs\WorldChamp2018.pgn'
.\.venv\Scripts\python.exe -m pytest tests/test_pgn_boundaries_original.py
```

That optional regression checks all 15 games and 1,528 moves and verifies the
source bytes remain unchanged. It skips when the variable is unset. The original
file and its private location are not included in source or release packages.

The Chess960 audit includes synthetic castling/input tests and an optional
regression against the original `Chess960_10_Great_Games.pgn`:

```powershell
$env:PGN_PLAYER_CHESS960_PGN = 'C:\PGNs\Chess960_10_Great_Games.pgn'
.\.venv\Scripts\python.exe -m pytest tests/test_chess960_core.py tests/test_chess960_ui.py tests/test_chess960_original.py --basetemp=.build/chess960-tests
```

The original-file tests verify all 10 games, 864 plies, 14 castlings, and
application export/reimport without changing the source file. They skip when
the variable is unset. See [the compatibility audit](CHESS960_AUDIT.md) for
coverage and limitations. The original collection is not bundled.

The compiler runs with a restricted dependency search path containing Python
and Windows system directories. This prevents unrelated tools on the host's
PATH from contributing incompatible DLLs to a release.

Fixed outputs:

```text
dist/
  portable/PGN Player/PGN Player.exe
  PGN Player Portable.zip
  PGN Player Setup.exe
  PGN Player Source.zip
  SHA256SUMS.txt
```

The installer's default launch path is
`%LOCALAPPDATA%\Programs\PGN Player\PGN Player.exe`.
The executable name is identical for both editions, but their directories and
user data are separate. There are no VBS launchers. Temporary compiler payloads
live only in `.build/payload` and are removed after a successful build. Other
compiler intermediates and offscreen QA reports remain in `.build`.

## Background validation

Tests use Qt's offscreen platform, not live desktop control. Packaged smoke
tests can also be invoked from a background process with:

```text
"PGN Player.exe" --smoke-test --smoke-output <absolute report path>
```

This internal check uses temporary data, loads a generated two-game PGN,
navigates moves/games, verifies saved-index reuse, renders both themes, and
checks the sound assets, default-off setting, and saved Sound menu state without
playing audio. It creates a JSON report
plus light/dark PNGs beside it. No ordinary user storage is read or written.

Move and capture WAVs are bundled in `src/pgn_player/assets/`. To reproduce them
from the included recorded samples, run
`.\.venv\Scripts\python.exe scripts\generate_sounds.py`.
This works offline without an audio converter. The source recordings, CC0
provenance, and initial conversion details are in `scripts/sound_sources/`.
Playback uses Windows' built-in audio support and needs no extra dependency.

Builds are unsigned unless a distributor separately signs the resulting
executable/installer with their own certificate. This project contains no
signing credentials or private paths. Dependency downloads do not become
runtime network requirements.
