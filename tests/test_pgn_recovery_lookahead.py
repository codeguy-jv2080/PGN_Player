"""Lexical variation recovery remains bounded and preserves seek cookies."""

import io
import random

import pytest

from pgn_player.pgn_recovery import VariationRecovery


class CountingStream(io.StringIO):
    def __init__(self, value):
        super().__init__(value)
        self.reads = 0

    def readline(self, *args):
        self.reads += 1
        return super().readline(*args)


def _header_positions(lines):
    offset = 0
    for number, line in enumerate(lines, 1):
        offset += len(line)
        if line.startswith("["):
            yield number, offset


@pytest.mark.parametrize(
    ("following", "depth", "recover"),
    [
        ("d5) 2. c4 *\n", 1, False),
        ("d5 (1...Nf6) 2.c4 *\n", 1, True),
        ('{ ) * [Event "Comment"] }\nd5) *\n', 1, False),
        ('; ) *\n% ) *\n[Site "*)"]\nd5 *\n', 1, True),
        ("d5 (Nf6)\n", 1, True),
        ("d5 (Nf6))\n", 1, False),
        ("d5 (Nf6))\n", 2, True),
        ("{First line\n; ) *\nStill a comment} d5) *\n", 1, False),
        ("d5 ; ) *\n) 1-0\n", 1, False),
        ("d5 1/2-1/2\n)\n", 1, True),
        ("d5 0-1\n)\n", 1, True),
        ("d5*\n)\n", 1, True),
        ("word1-0word ) *\n", 1, False),
    ],
)
def test_structure_respects_comments_quoted_tags_and_termination(following, depth, recover):
    stream = io.StringIO(following)
    assert VariationRecovery().recovery_needed(stream, depth, 1) is recover
    assert stream.tell() == 0


def test_cached_suffix_uses_each_readers_own_variation_depth():
    lines = [
        '[Event "A"]\n',
        "(\n",
        '[Site "B"]\n',
        ")\n",
        '[Event "C"]\n',
        ")\n",
        '[Event "D"]\n',
        "*\n",
    ]
    stream = CountingStream("".join(lines))
    context = VariationRecovery()
    expected_drops = {1: 1, 3: 2, 5: 1, 7: 0}
    first_probe_reads = None
    for line_number, cookie in _header_positions(lines):
        for depth in (1, 2, 3):
            stream.seek(cookie)
            assert context.recovery_needed(stream, depth, line_number) is (
                depth > expected_drops[line_number]
            )
            assert stream.tell() == cookie
            if first_probe_reads is None:
                first_probe_reads = stream.reads
            assert stream.reads == first_probe_reads


def test_equal_outcome_ranges_and_pruning_do_not_hide_later_candidates():
    lines = [
        '[Event "A"]\n',
        '[Site "A"]\n',
        "e4 e5\n",
        '[White "A"]\n',
        ") (\n",
        '[Event "B"]\n',
        '[Site "B"]\n',
        "(\n",
        '[Event "C"]\n',
        ")\n",
        "*\n",
    ]
    stream = CountingStream("".join(lines))
    context = VariationRecovery()
    initial_reads = None
    for line_number, cookie in _header_positions(lines):
        stream.seek(cookie)
        expected = line_number in (6, 7)
        assert context.recovery_needed(stream, 1, line_number) is expected
        if initial_reads is None:
            initial_reads = stream.reads
        assert stream.reads == initial_reads
        assert stream.tell() == cookie

    # Reusing a context after seeking backwards must not use pruned ranges.
    stream.seek(len(lines[0]))
    assert context.recovery_needed(stream, 1, 1) is False
    assert stream.reads > initial_reads


def test_cancellation_during_probe_restores_stream_and_allows_a_retry():
    class Cancelled(Exception):
        pass

    checks = 0
    should_cancel = True

    def cancel():
        nonlocal checks
        checks += 1
        if should_cancel and checks == 2:
            raise Cancelled()

    prefix = '[Event "Recovery candidate"]\n'
    stream = CountingStream(prefix + "e4 e5\n" * 5000)
    stream.seek(len(prefix))
    cookie = stream.tell()
    context = VariationRecovery(cancel)
    with pytest.raises(Cancelled):
        context.recovery_needed(stream, 1, 1)
    assert stream.tell() == cookie
    assert stream.reads == 2048

    should_cancel = False
    assert context.recovery_needed(stream, 1, 1) is True
    assert stream.tell() == cookie


def test_stream_read_failure_also_restores_position():
    class FailingStream(CountingStream):
        def readline(self, *args):
            if self.reads == 3:
                raise OSError("Synthetic read failure")
            return super().readline(*args)

    stream = FailingStream("prefix\n" + "e4 e5\n" * 20)
    stream.seek(len("prefix\n"))
    cookie = stream.tell()
    with pytest.raises(OSError, match="Synthetic read failure"):
        VariationRecovery().recovery_needed(stream, 1, 1)
    assert stream.tell() == cookie


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_real_text_stream_retains_opaque_cookie_with_unicode(tmp_path, newline):
    path = tmp_path / "unicode.pgn"
    text = newline.join([
        '[Event "Étude ♜"]',
        '{A comment with ) and *',
        '[Event "Not a game"]}',
        '1. d4 d5) *',
        '',
    ])
    path.write_bytes(text.encode("utf-8-sig"))
    with path.open(encoding="utf-8-sig", newline=None) as stream:
        stream.readline()
        cookie = stream.tell()
        assert VariationRecovery().recovery_needed(stream, 1, 1) is False
        assert stream.tell() == cookie
        assert stream.readline().startswith("{A comment")


def test_many_missing_result_headers_scan_the_suffix_only_once():
    lines = []
    for index in range(4000):
        lines.extend([
            f'[Event "Study {index}"]\n',
            '[Result "*"]\n',
            '1. e4 (1. d4 (1. c4 e5) d5) e5\n',
        ])
    stream = CountingStream("".join(lines))
    context = VariationRecovery()
    for line_number, cookie in _header_positions(lines):
        stream.seek(cookie)
        assert context.recovery_needed(stream, 1, line_number) is True
        assert stream.tell() == cookie
    assert stream.reads == len(lines)
    # Complete header sets plus balanced RAVs need one compact cached range,
    # even though this malformed collection has thousands of missing results.
    assert len(context._starts) == 1


def test_cached_suffix_matches_independent_probes_across_nesting_changes():
    randomizer = random.Random(42)
    for _ in range(50):
        lines = ['[Event "First"]\n'] + [
            randomizer.choice([
                '(\n', ')\n', '[Event "Candidate"]\n', 'e4 e5\n',
                '{ (* ) }\n', '; ) *\n', '*\n',
            ])
            for _ in range(50)
        ]
        shared = VariationRecovery()
        stream = CountingStream("".join(lines))
        for line_number, cookie in _header_positions(lines):
            for depth in (1, 2, 3):
                stream.seek(cookie)
                independent = io.StringIO(stream.getvalue())
                independent.seek(cookie)
                assert shared.recovery_needed(stream, depth, line_number) == (
                    VariationRecovery().recovery_needed(independent, depth, line_number)
                )
                assert stream.tell() == cookie
        assert stream.reads <= len(lines) + 1
