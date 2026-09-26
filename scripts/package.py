"""Publish one portable folder plus clean ZIP/source archives from a clean payload.

The live portable data directory is never a packaging input and is never removed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".bak", ".backup"}
SOURCE_DIRS = ("src", "tests", "docs", "scripts", "packaging", "licenses")
SOURCE_FILES = (".gitignore", "pyproject.toml", "requirements.lock", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md")


def clean_relative(path: Path) -> bool:
    return not any(part in {"data", ".local-data", "__pycache__", ".pytest_cache", ".venv", ".git"} for part in path.parts) and path.suffix.lower() not in PRIVATE_SUFFIXES and not path.name.endswith((".pyc", ".pyo", "-wal", "-shm", "-journal"))


def publish() -> None:
    payload = ROOT / ".build" / "payload" / "PGN Player"
    if not (payload / "PGN Player.exe").is_file():
        raise RuntimeError("Build the clean application payload first.")
    distribution = ROOT / "dist"
    portable = distribution / "portable" / "PGN Player"
    portable.mkdir(parents=True, exist_ok=True)
    inputs = {p.relative_to(payload): p for p in payload.rglob("*") if p.is_file()}
    for relative in inputs:
        if not clean_relative(relative):
            raise RuntimeError(f"Private runtime file found in build payload: {relative}")
    inputs[Path("edition.json")] = ROOT / "packaging" / "portable.json"
    inputs[Path("README.md")] = ROOT / "README.md"
    inputs[Path("SHORTCUTS.md")] = ROOT / "docs" / "SHORTCUTS.md"
    # Update only prior manifest-owned application files. Never recurse through
    # the portable user's data, and never remove files that were not ours.
    manifest_path = portable / "application-files.json"
    stale = []
    if manifest_path.is_file():
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        for old in prior:
            relative = Path(old)
            destination = (portable / relative).resolve()
            if relative.is_absolute() or not destination.is_relative_to(portable.resolve()) or not clean_relative(relative):
                raise RuntimeError("Unsafe application manifest entry; update stopped without removing it.")
            if relative not in inputs and destination.is_file():
                stale.append(destination)
    # Check existing files for locks/read-only access before removing or
    # overwriting any application file. This never opens user data.
    for destination in [*(portable / relative for relative in inputs), manifest_path, *stale]:
        if destination.is_file():
            descriptor = os.open(destination, os.O_WRONLY | getattr(os, "O_BINARY", 0))
            os.close(descriptor)
    for destination in stale:
        destination.unlink()
    for relative, source in inputs.items():
        destination = portable / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    (portable / "data").mkdir(exist_ok=True)
    manifest = json.dumps(sorted(p.as_posix() for p in inputs), indent=2)
    manifest_path.write_text(manifest, encoding="utf-8")
    # Archive the clean payload mapping, NEVER the live portable directory.
    with zipfile.ZipFile(distribution / "PGN Player Portable.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("PGN Player/data/", "")
        for relative, source in sorted(inputs.items()):
            archive.write(source, "PGN Player/" + relative.as_posix())
        archive.writestr("PGN Player/application-files.json", manifest)
    with zipfile.ZipFile(distribution / "PGN Player Source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in SOURCE_FILES:
            archive.write(ROOT / name, "PGN Player/" + name)
        for directory in SOURCE_DIRS:
            for source in sorted((ROOT / directory).rglob("*")):
                relative = source.relative_to(ROOT)
                if source.is_file() and clean_relative(relative):
                    archive.write(source, "PGN Player/" + relative.as_posix())
    print("Updated canonical portable folder; wrote clean portable and source ZIPs.")


if __name__ == "__main__":
    publish()
