"""Explicit storage locations for development, portable, and installed editions."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import tempfile


class StoragePathError(RuntimeError):
    """The selected edition's storage cannot be resolved or written."""


@dataclass(frozen=True)
class AppPaths:
    edition: str
    app_dir: Path
    data_dir: Path
    db_path: Path


def resolve_paths(
    edition: str | None = None,
    app_dir: str | Path | None = None,
    data_override: str | Path | None = None,
) -> AppPaths:
    """Resolve and check this edition's own storage, with no fallback location.

    Frozen builds require ``edition.json`` beside the executable. Development
    defaults to ``.local-data`` in the project. ``data_override`` is an explicit
    test hook; it is not exposed as an environment variable or user preference.
    This function never reads, copies, or migrates another edition's files.
    """
    frozen = bool(getattr(sys, "frozen", False))
    root = Path(app_dir) if app_dir is not None else (
        Path(sys.executable).parent if frozen else Path(__file__).resolve().parents[2]
    )
    root = root.expanduser().resolve()

    if frozen:
        config = root / "edition.json"
        try:
            payload = json.loads(config.read_text(encoding="utf-8-sig"))
            configured = payload.get("edition") if isinstance(payload, dict) else None
        except (OSError, ValueError) as exc:
            raise StoragePathError(
                f"Cannot read the application edition from {config}. "
                "Restore this file from the same edition's package; no other storage was used."
            ) from exc
        if configured not in {"portable", "installed"}:
            raise StoragePathError(f"Invalid application edition in {config}.")
        if edition is not None and edition != configured:
            raise StoragePathError("The requested edition conflicts with edition.json.")
        edition = configured
    elif edition is None:
        edition = "development"

    if edition not in {"development", "portable", "installed"}:
        raise StoragePathError(f"Unknown application edition: {edition!r}.")

    if data_override is not None:
        data_dir = Path(data_override).expanduser().resolve()
    elif edition == "portable":
        data_dir = root / "data"
    elif edition == "installed":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data or not Path(local_app_data).is_absolute():
            raise StoragePathError(
                "LOCALAPPDATA is unavailable. The installed edition cannot locate its own storage."
            )
        data_dir = Path(local_app_data) / "PGN Player" / "Installed" / "data"
    else:
        data_dir = root / ".local-data"

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        # Test the chosen directory rather than silently choosing a writable
        # location belonging to another edition. Only our temporary file is removed.
        with tempfile.TemporaryFile(prefix=".pgn-player-write-", dir=data_dir):
            pass
    except OSError as exc:
        raise StoragePathError(
            f"The {edition} edition cannot write its data folder: {data_dir}. "
            "Give this folder write permission or move the portable application to a writable folder."
        ) from exc

    return AppPaths(edition, root, data_dir, data_dir / "pgn-player.sqlite3")
