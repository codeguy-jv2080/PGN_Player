"""Recorded chess-piece provenance and waveform checks without audio devices."""

import hashlib
import importlib.util
from pathlib import Path
import struct
import wave

import pytest

from pgn_player import sound


PROJECT = Path(__file__).resolve().parents[1]
ASSETS = Path(sound.__file__).parent / "assets"
SOURCE = PROJECT / "scripts" / "sound_sources" / "chess-pieces-close.wav"
SOURCE_HASH = "3e3ab886da26b0c89804ae0842ea7eb68c4375511f97291a63d66f729e5f7cff"
SELECTIONS = [("move.wav", 538_189, 0.32), ("capture.wav", 664_345, 0.40)]


def read_sound(path):
    with wave.open(str(path), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getcomptype() == "NONE"
        rate = audio.getframerate()
        assert rate == 22_050
        raw = audio.readframes(audio.getnframes())
    return rate, struct.unpack(f"<{len(raw) // 2}h", raw)


@pytest.fixture(scope="module")
def recording():
    return read_sound(SOURCE)


def test_complete_recorded_source_provenance_is_unchanged(recording):
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == SOURCE_HASH
    rate, samples = recording
    assert len(samples) / rate == pytest.approx(48.98231292517007)


@pytest.mark.parametrize("filename,start,previous_level", SELECTIONS)
def test_cues_keep_approved_louder_levels_and_complete_decay(filename, start, previous_level):
    rate, samples = read_sound(ASSETS / filename)
    assert len(samples) == 6_615
    assert len(samples) / rate == 0.300
    assert samples[0] == samples[-1] == 0
    peak = max(abs(sample) for sample in samples)
    assert peak / 32767 == pytest.approx(previous_level * 10 ** (6 / 20), abs=1 / 32767)
    assert peak < 32767
    assert abs(samples[1]) < peak * 0.005
    assert abs(samples[-2]) < peak * 0.005
    # The final room-tone portion is far below the physical impact's level.
    tail = samples[-round(rate * 0.020):]
    assert (sum(sample * sample for sample in tail) / len(tail)) ** 0.5 < peak * 0.01


@pytest.mark.parametrize("filename,start,previous_level", SELECTIONS)
def test_correct_excerpt_preserves_attack_and_decay_with_only_uniform_gain(recording, filename, start, previous_level):
    rate, recorded = recording
    _, cue = read_sound(ASSETS / filename)
    excerpt = recorded[start:start + len(cue)]
    recorded_peak = max(range(len(excerpt)), key=lambda index: abs(excerpt[index]))
    cue_peak = max(range(len(cue)), key=lambda index: abs(cue[index]))
    assert cue_peak == recorded_peak
    assert 0.005 < cue_peak / rate < 0.008
    # Fit one gain value across the attack and full decay, excluding only the
    # ambient edge fades. A wrong source, crop, EQ or time stretch will fail.
    interior = range(round(rate * 0.001), round(rate * 0.280))
    gain = sum(excerpt[i] * cue[i] for i in interior) / sum(excerpt[i] ** 2 for i in interior)
    residual = sum((cue[i] - gain * excerpt[i]) ** 2 for i in interior) / len(interior)
    assert residual ** 0.5 < 0.6
    # The tail after the former 90 ms cutoff remains the original recording,
    # not silence or an artificially prolonged/synthesized replacement.
    late = range(round(rate * 0.090), round(rate * 0.180))
    assert sum(cue[i] ** 2 for i in late) > 0
    assert max(abs(cue[i] - gain * excerpt[i]) for i in late) < 1


@pytest.mark.parametrize("pattern", [("move.wav",), ("capture.wav",), ("move.wav", "capture.wav"), ("capture.wav", "move.wav")])
def test_staggered_natural_decays_at_ten_moves_per_second_do_not_clip(pattern):
    clips = {name: read_sound(ASSETS / name)[1] for name in set(pattern)}
    interval = 2_205  # 100 ms at 22,050 Hz, the fastest supported autoplay.
    mixed = [0] * (interval * 7 + 6_615)
    for index in range(8):
        clip = clips[pattern[index % len(pattern)]]
        for offset, sample in enumerate(clip):
            mixed[index * interval + offset] += sample
    assert max(abs(sample) for sample in mixed) < 32767


def test_assets_reproduce_exactly_offline_from_included_recording(tmp_path):
    script = PROJECT / "scripts" / "generate_sounds.py"
    spec = importlib.util.spec_from_file_location("generate_sounds", script)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    generator.generate_sounds(tmp_path)
    for filename in ("move.wav", "capture.wav"):
        assert (tmp_path / filename).read_bytes() == (ASSETS / filename).read_bytes()
