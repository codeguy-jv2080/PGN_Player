# Build PGN Player on Windows

## Requirements

- Windows 10/11 x64
- Python 3.10 or later (the supplied build was verified with 3.10.6 x64)
- Inno Setup 6 for installer compilation
- Internet access only to obtain development dependencies

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
closes. It creates a JSON report
plus light/dark PNGs beside it. No ordinary user storage is read or written.

Builds are unsigned unless a distributor separately signs the resulting
executable/installer with their own certificate. This project contains no
signing credentials or private paths. Dependency downloads do not become
runtime network requirements.
