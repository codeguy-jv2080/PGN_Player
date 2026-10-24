"""Prepare PGN Player cues from the included chess-piece recording.

Uses the included decoded WAV sources in sound_sources, entirely offline.
Processing is limited to trimming, gain and tiny edge fades. No synthesis,
equalization, pitch shifting, external tools or audio devices are involved.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import wave


SAMPLE_RATE = 22_050
SOURCE_DIR = Path(__file__).resolve().parent / "sound_sources"
SOURCE_FILE = SOURCE_DIR / "chess-pieces-close.wav"
# Retain the louder peak levels approved for the earlier cues.
GAIN_DB = 6.0
OUTPUT_GAIN = 10 ** (GAIN_DB / 20)
# Start frames include 5 ms of room tone before the selected physical contact.
# The 300 ms excerpts preserve the complete natural decay; playback can overlap
# them on separate voices when moves occur faster than one excerpt's duration.
SOUNDS = (
    ("move.wav", 538_189, 6_615, 0.32 * OUTPUT_GAIN),
    ("capture.wav", 664_345, 6_615, 0.40 * OUTPUT_GAIN),
)


def _prepare_recording(source: Path, start_frame: int, count: int, target_peak: float) -> bytes:
    with wave.open(str(source), "rb") as recording:
        if (
            recording.getnchannels() != 1
            or recording.getsampwidth() != 2
            or recording.getframerate() != SAMPLE_RATE
            or recording.getcomptype() != "NONE"
        ):
            raise ValueError(f"Expected mono 16-bit {SAMPLE_RATE} Hz PCM: {source.name}")
        if start_frame < 0 or recording.getnframes() < start_frame + count:
            raise ValueError(f"Recording is too short: {source.name}")
        recording.setpos(start_frame)
        raw = recording.readframes(count)
    samples = struct.unpack(f"<{count}h", raw)
    peak = max(abs(sample) for sample in samples)
    if not peak:
        raise ValueError(f"Recording contains no audible samples: {source.name}")
    gain = target_peak * 32767 / peak
    # These fades touch only ambient room tone: 0.5 ms before the attack and
    # 10 ms after the natural decay has settled. The hit itself is gain-only.
    fade_in = round(SAMPLE_RATE * 0.0005)
    fade_out = round(SAMPLE_RATE * 0.010)
    pcm = [
        round(sample * gain * min(1.0, index / fade_in, (count - 1 - index) / fade_out))
        for index, sample in enumerate(samples)
    ]
    return struct.pack(f"<{len(pcm)}h", *pcm)


def generate_sounds(output_dir: Path) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, start_frame, count, target_peak in SOUNDS:
        pcm = _prepare_recording(SOURCE_FILE, start_frame, count, target_peak)
        with wave.open(str(output_dir / filename), "wb") as sound:
            sound.setnchannels(1)
            sound.setsampwidth(2)
            sound.setframerate(SAMPLE_RATE)
            sound.writeframes(pcm)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path(__file__).resolve().parents[1] / "src" / "pgn_player" / "assets",
    )
    args = parser.parse_args()
    generate_sounds(args.output_dir)


if __name__ == "__main__":
    main()
