"""Bounded source checks, not a claim of whole-file content verification."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable


SAMPLE_BYTES = 4096
SAMPLE_COUNT = 8
MAX_SAMPLE_BYTES = SAMPLE_BYTES * SAMPLE_COUNT


def sample_ranges(size: int) -> tuple[tuple[int, int], ...]:
    """At most 32 KiB, including the beginning and end of any sized file."""
    if size < 0:
        raise ValueError("Source size cannot be negative.")
    if size <= MAX_SAMPLE_BYTES:
        return ((0, size),)
    return tuple(((size - SAMPLE_BYTES) * index // (SAMPLE_COUNT - 1), SAMPLE_BYTES)
                 for index in range(SAMPLE_COUNT))


def source_sample(path: Path, size: int, *, cancel: Callable[[], None] | None = None) -> str:
    """Hash fixed windows with constant I/O and memory, preserving source bytes.

    ``cancel`` is a checkpoint that raises when cancelled. Changes between the
    windows can go undetected; a saved game's separate digest protects resume.
    Unbuffered reads make the byte budget independent of the source's size.
    """
    digest = hashlib.sha256(b"pgn-source-sample-v1\0" + str(size).encode("ascii"))
    with path.open("rb", buffering=0) as stream:
        for offset, length in sample_ranges(size):
            stream.seek(offset)
            digest.update(f"\0{offset}:{length}:".encode("ascii"))
            remaining = length
            while remaining:
                if cancel is not None:
                    cancel()
                data = stream.read(remaining)
                if not data:
                    raise OSError("The PGN changed or could not be read during cache validation.")
                digest.update(data)
                remaining -= len(data)
    return digest.hexdigest()
