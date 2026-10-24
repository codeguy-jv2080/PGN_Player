"""WinMM queue/lifetime checks with a fake driver; never open an audio device."""

import ctypes
import gc
from pathlib import Path
from types import SimpleNamespace
import wave
import weakref

import pytest

from pgn_player import sound
from pgn_player.sound import MoveSounds, WAVEFORMATEX, WAVEHDR, WHDR_DONE


ASSETS = Path(sound.__file__).parent / "assets"


def header(pointer):
    return ctypes.cast(pointer, ctypes.POINTER(WAVEHDR)).contents


class FakeWinMM:
    """Retain native addresses only, so tests detect missing Python ownership."""

    def __init__(self):
        self.calls = []
        self.writes = []
        self.write_handles = []
        self.write_starts = []
        self.prepared = set()
        self.pending = set()
        self.handles = set()
        self.owners = {}
        self.ends = {}
        self.time = 0.0
        self.errors = {}
        self.next_handle = 0x123456789 if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x12345678

    def result(self, name, handle=None):
        result = self.errors.get((name, handle), self.errors.get(name, 0))
        if isinstance(result, list):
            result = result.pop(0) if result else 0
        if isinstance(result, Exception):
            raise result
        return result

    def waveOutOpen(self, target, mapper, format_pointer, callback, instance, flags):
        self.calls.append("open")
        assert mapper == sound.WAVE_MAPPER
        assert callback == instance == flags == 0
        format_ = ctypes.cast(format_pointer, ctypes.POINTER(WAVEFORMATEX)).contents
        assert (format_.wFormatTag, format_.nChannels, format_.nSamplesPerSec,
                format_.nAvgBytesPerSec, format_.nBlockAlign, format_.wBitsPerSample,
                format_.cbSize) == (1, 1, 22050, 44100, 2, 16, 0)
        result = self.result("open")
        if not result:
            ctypes.cast(target, ctypes.POINTER(ctypes.c_void_p))[0] = self.next_handle
            self.handles.add(self.next_handle)
            self.next_handle += 1
        return result

    def waveOutPrepareHeader(self, handle, pointer, size):
        self.calls.append("prepare")
        assert handle.value in self.handles
        assert size == ctypes.sizeof(WAVEHDR)
        value = header(pointer)
        assert value.dwFlags == value.dwUser == value.dwLoops == 0
        result = self.result("prepare", handle.value)
        if not result:
            value.dwFlags |= 2
            self.prepared.add(ctypes.addressof(value))
            self.owners[ctypes.addressof(value)] = handle.value
        return result

    def waveOutWrite(self, handle, pointer, size):
        self.calls.append("write")
        value = header(pointer)
        address = ctypes.addressof(value)
        assert address in self.prepared
        assert self.owners[address] == handle.value
        assert size == ctypes.sizeof(WAVEHDR)
        result = self.result("write", handle.value)
        if not result:
            start = max([self.time] + [self.ends[item] for item in self.pending
                                     if self.owners[item] == handle.value])
            value.dwFlags = (value.dwFlags & ~WHDR_DONE) | 0x10
            self.pending.add(address)
            self.ends[address] = start + value.dwBufferLength / 44100
            self.writes.append((address, value.lpData, ctypes.string_at(value.lpData, value.dwBufferLength)))
            self.write_handles.append(handle.value)
            self.write_starts.append(start)
        return result

    def finish(self, address):
        value = WAVEHDR.from_address(address)
        value.dwFlags = (value.dwFlags & ~0x10) | WHDR_DONE
        self.pending.discard(address)

    def advance(self, seconds):
        self.time += seconds
        for address in list(self.pending):
            if self.ends[address] <= self.time + 1e-9:
                self.finish(address)

    def waveOutUnprepareHeader(self, handle, pointer, size):
        self.calls.append("unprepare")
        value = header(pointer)
        address = ctypes.addressof(value)
        assert size == ctypes.sizeof(WAVEHDR)
        assert self.owners[address] == handle.value
        result = self.result("unprepare", handle.value)
        if address in self.pending:
            return 33
        if not result:
            value.dwFlags &= ~2
            self.prepared.discard(address)
            self.owners.pop(address)
            self.ends.pop(address, None)
        return result

    def waveOutReset(self, handle):
        self.calls.append("reset")
        result = self.result("reset", handle.value)
        if not result:
            for address in list(self.pending):
                if self.owners[address] == handle.value:
                    self.finish(address)
        return result

    def waveOutClose(self, handle):
        self.calls.append("close")
        assert handle.value not in self.owners.values()
        result = self.result("close", handle.value)
        if not result:
            self.handles.remove(handle.value)
        return result


@pytest.fixture
def make_service():
    services = []
    original_outputs = list(sound._OPEN_OUTPUTS)

    def create(**kwargs):
        backend = kwargs.pop("backend", FakeWinMM())
        service = MoveSounds(backend=backend, **kwargs)
        services.append((service, backend))
        return service, backend

    yield create
    for service, backend in services:
        if isinstance(backend, FakeWinMM):
            backend.errors.clear()
        service.close()
    assert sound._OPEN_OUTPUTS == original_outputs


def pcm(filename):
    with wave.open(str(ASSETS / filename), "rb") as audio:
        return audio.readframes(audio.getnframes())


def write_silent_assets(directory, move_seconds=0.3, capture_seconds=0.4):
    for filename, duration in (("move.wav", move_seconds), ("capture.wav", capture_seconds)):
        with wave.open(str(directory / filename), "wb") as audio:
            audio.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * round(duration * 22050))


def test_disabled_and_enabled_without_play_never_open_device(make_service):
    service, backend = make_service()
    assert not service.enabled
    service.play()
    service.play(capture=True)
    service.stop()
    service.set_enabled(True)
    service.stop()
    service.set_enabled(False)
    assert backend.calls == []


def test_rapid_requests_preserve_every_complete_buffer_across_eight_voices(make_service):
    service, backend = make_service()
    service.set_enabled(True)
    for index in range(40):
        service.play(capture=bool(index % 2))
    assert backend.calls.count("open") == 8
    assert len(set(backend.write_handles[:8])) == 8
    assert len(backend.writes) == 40
    assert len({address for address, _, _ in backend.writes}) == 40
    assert len({data for _, data, _ in backend.writes}) == 40
    assert sum(len(output.buffers) for output in service._outputs) == 40
    assert "reset" not in backend.calls and "close" not in backend.calls
    assert [data for _, _, data in backend.writes] == [pcm("capture.wav" if index % 2 else "move.wav") for index in range(40)]
    gc.collect()
    for address, data, expected in backend.writes:
        assert WAVEHDR.from_address(address).lpData == data
        assert ctypes.string_at(data, len(expected)) == expected


def test_completed_buffers_are_unprepared_before_releasing_memory(make_service):
    service, backend = make_service()
    service.set_enabled(True)
    service.play()
    service.play(capture=True)
    first = weakref.ref(service._outputs[0].buffers[0])
    second = weakref.ref(service._outputs[1].buffers[0])
    backend.finish(backend.writes[0][0])
    service.play()
    gc.collect()
    assert first() is None
    assert second() is not None
    assert sum(len(output.buffers) for output in service._outputs) == 2
    assert backend.calls.count("open") == 2
    assert backend.write_handles == [backend.write_handles[0], backend.write_handles[1], backend.write_handles[0]]
    assert backend.calls[-3:] == ["unprepare", "prepare", "write"]


def test_saturated_pool_queues_on_least_pending_bytes_without_cutting_tails(make_service, tmp_path):
    write_silent_assets(tmp_path)
    service, backend = make_service(assets_dir=tmp_path)
    service.set_enabled(True)
    for index in range(8):
        service.play(capture=index != 3)
    initial_handles = list(backend.write_handles)
    service.play(capture=True)
    service.play()
    assert backend.write_handles[-2:] == [initial_handles[3], initial_handles[0]]
    assert backend.write_starts[-2:] == pytest.approx([0.3, 0.4])
    assert backend.calls.count("open") == 8
    assert len(backend.pending) == 10
    assert "reset" not in backend.calls and "close" not in backend.calls
    assert [len(data) for _, _, data in backend.writes[-2:]] == [17640, 13230]


def test_long_decays_at_100ms_cadence_start_immediately_and_reuse_voices(make_service, tmp_path):
    write_silent_assets(tmp_path)
    service, backend = make_service(assets_dir=tmp_path)
    service.set_enabled(True)
    requested_at = []
    for index in range(100):
        if index:
            backend.advance(0.1)
        requested_at.append(backend.time)
        service.play(capture=bool(index % 2))
    assert backend.write_starts == pytest.approx(requested_at)
    assert len(backend.writes) == 100
    assert backend.calls.count("open") == 4
    assert len(backend.handles) == 4
    assert len(backend.pending) <= 4
    assert "reset" not in backend.calls and "close" not in backend.calls
    assert [len(data) for _, _, data in backend.writes] == [17640 if index % 2 else 13230 for index in range(100)]


def test_device_with_one_available_voice_still_queues_complete_requests(make_service):
    service, backend = make_service()
    service.set_enabled(True)
    service.play()
    backend.errors["open"] = 4
    service.play(capture=True)
    assert len(service._outputs) == 1
    assert len(backend.writes) == 2
    assert backend.write_handles[0] == backend.write_handles[1]
    assert backend.writes[1][2] == pcm("capture.wav")
    assert "reset" not in backend.calls


def test_assets_decode_once_across_requests_and_output_reopens(make_service, monkeypatch):
    original = wave.open
    reads = []

    def tracked(filename, mode):
        reads.append(Path(filename).name)
        return original(filename, mode)

    monkeypatch.setattr(sound.wave, "open", tracked)
    service, backend = make_service()
    service.set_enabled(True)
    service.play()
    service.play(capture=True)
    service.stop()
    service.play()
    assert reads == ["move.wav", "capture.wav"]
    assert backend.calls.count("open") == 3
    assert len(backend.writes) == 3


def test_mute_cancels_all_queued_audio_and_reenable_starts_fresh(make_service):
    service, backend = make_service()
    service.set_enabled(True)
    for _ in range(3):
        service.play()
    service.set_enabled(False)
    assert not service.enabled
    assert service._outputs == []
    assert not backend.pending and not backend.prepared
    assert backend.calls[-9:] == ["reset", "unprepare", "close"] * 3
    assert backend.handles == set()
    calls = list(backend.calls)
    service.play()
    service.stop()
    service.set_enabled(False)
    assert backend.calls == calls
    service.set_enabled(True)
    service.play(capture=True)
    assert backend.calls.count("open") == 4
    assert len(backend.writes) == 4


@pytest.mark.parametrize("operation", ["prepare", "write"])
def test_transient_rejection_reopens_once_and_submits_no_duplicate(make_service, operation):
    service, backend = make_service()
    backend.errors[operation] = [6, 0]
    service.set_enabled(True)
    service.play(capture=True)
    assert backend.calls.count("open") == 2
    assert len(backend.writes) == 1
    assert backend.writes[0][2] == pcm("capture.wav")
    backend.finish(backend.writes[0][0])
    service.play()
    assert len(backend.writes) == 2
    assert backend.calls.count("open") == 2


@pytest.mark.parametrize("operation", ["prepare", "write"])
def test_persistent_rejection_is_bounded_and_future_request_can_recover(make_service, operation):
    service, backend = make_service()
    backend.errors[operation] = 6
    service.set_enabled(True)
    service.play()
    assert backend.calls.count("open") == 2
    assert backend.writes == []
    assert service._outputs == []
    backend.errors.clear()
    service.play()
    assert len(backend.writes) == 1
    assert service.enabled


@pytest.mark.parametrize("error", [6, OSError("Device unavailable")])
def test_failed_open_does_not_leave_an_output_and_next_request_retries(make_service, error):
    service, backend = make_service()
    backend.errors["open"] = error
    service.set_enabled(True)
    service.play()
    assert service._outputs == []
    assert backend.calls == ["open"]
    backend.errors.clear()
    service.play()
    assert len(backend.writes) == 1


def test_uncertain_write_is_not_replayed_and_cleanup_releases_memory(make_service):
    service, backend = make_service()
    original_write = backend.waveOutWrite

    def accepted_then_failed(*args):
        original_write(*args)
        raise RuntimeError("Native return was unavailable")

    backend.waveOutWrite = accepted_then_failed
    service.set_enabled(True)
    service.play()
    assert len(backend.writes) == 1
    assert backend.calls.count("open") == 1
    assert service._outputs == []
    assert not backend.pending and not backend.prepared


@pytest.mark.parametrize("error", [33, RuntimeError("Device unavailable")])
def test_failed_reset_retains_native_memory_until_successful_cleanup(make_service, error):
    service, backend = make_service()
    service.set_enabled(True)
    service.play()
    output = service._outputs[0]
    buffer = weakref.ref(output.buffers[0])
    backend.errors["reset"] = error
    service.set_enabled(False)
    gc.collect()
    assert output in sound._OPEN_OUTPUTS
    assert buffer() is not None
    assert "close" not in backend.calls
    assert backend.pending
    backend.errors.clear()
    service.stop()
    gc.collect()
    assert buffer() is None
    assert output not in sound._OPEN_OUTPUTS
    assert service._outputs == []


def test_failed_unprepare_retains_done_buffer_and_prevents_close(make_service):
    service, backend = make_service()
    service.set_enabled(True)
    service.play()
    buffer = weakref.ref(service._outputs[0].buffers[0])
    backend.errors["unprepare"] = 33
    service.stop()
    gc.collect()
    assert not backend.pending
    assert buffer() is not None
    assert "close" not in backend.calls
    backend.errors.clear()
    service.stop()
    gc.collect()
    assert buffer() is None
    assert service._outputs == []


def test_failed_close_keeps_handle_for_retry(make_service):
    service, backend = make_service()
    service.set_enabled(True)
    service.play()
    output = service._outputs[0]
    handle = output.handle.value
    backend.errors["close"] = 6
    service.close()
    assert not service.enabled
    assert service._outputs == []
    assert service._retired == [output]
    assert output in sound._OPEN_OUTPUTS
    assert output.handle.value == handle
    assert output.buffers == []
    backend.errors.clear()
    service.close()
    assert output not in sound._OPEN_OUTPUTS
    assert service._outputs == []


@pytest.mark.parametrize("operation", ["prepare", "write"])
def test_stale_output_is_retired_so_fresh_device_works_without_replaying_old_cue(make_service, operation):
    service, backend = make_service()
    service.set_enabled(True)
    for _ in range(8):
        service.play()
    stale = service._outputs[0]
    stale_handle = stale.handle.value
    buffer = weakref.ref(stale.buffers[0])
    for name in (operation, "reset", "unprepare", "close"):
        backend.errors[name, stale_handle] = 6
    service.play(capture=True)
    assert backend.calls.count("open") == 9
    assert len(service._outputs) == 8 and stale not in service._outputs
    assert service._retired == [stale]
    gc.collect()
    assert stale in sound._OPEN_OUTPUTS and buffer() is not None
    assert len(backend.writes) == 9
    assert backend.writes[-1][2] == pcm("capture.wav")
    assert backend.write_handles[-1] not in backend.write_handles[:8]
    service.play()
    assert backend.calls.count("open") == 9
    assert len(backend.writes) == 10
    assert backend.writes[-1][2] == pcm("move.wav")
    assert backend.write_handles.count(stale_handle) == 1

    service.set_enabled(False)
    service.stop()
    service.play(capture=True)
    assert service._outputs == []
    assert service._retired == [stale]
    assert backend.calls.count("open") == 9  # Muting never opens another device.
    assert len(backend.writes) == 10
    assert backend.handles == {stale_handle}

    backend.errors.clear()
    service.stop()
    gc.collect()
    assert buffer() is None
    assert stale not in sound._OPEN_OUTPUTS and service._retired == []
    service.set_enabled(True)
    service.play()
    assert backend.calls.count("open") == 10
    assert backend.write_handles.count(stale_handle) == 1  # Accepted cue stays retired.


@pytest.mark.parametrize("method", ["stop", "close"])
def test_cleanup_failure_in_one_voice_does_not_leave_other_voices_playing(make_service, method):
    service, backend = make_service()
    service.set_enabled(True)
    for _ in range(8):
        service.play()
    stale = service._outputs[3]
    handle = stale.handle.value
    backend.errors["reset", handle] = RuntimeError("Device disconnected")
    getattr(service, method)()
    assert service._outputs == []
    assert service._retired == [stale]
    assert backend.calls.count("reset") == 8
    assert backend.calls.count("close") == 7
    assert backend.handles == {handle}
    assert len(backend.pending) == 1
    assert stale in sound._OPEN_OUTPUTS
    backend.errors.clear()
    service.stop()
    assert not backend.handles and not backend.pending and not backend.prepared
    assert service._retired == [] and stale not in sound._OPEN_OUTPUTS
    assert backend.calls.count("open") == 8


def test_native_ownership_survives_discarded_service(make_service):
    service, backend = make_service()
    service.set_enabled(True)
    service.play()
    output = service._outputs[0]
    reference = weakref.ref(output.buffers[0])
    # Simulate an owner losing its resource reference while Windows retains it.
    service._outputs.clear()
    gc.collect()
    assert reference() is not None
    assert output in sound._OPEN_OUTPUTS
    assert output.stop()


@pytest.mark.parametrize("invalid", ["missing", "malformed", "different_format", "truncated"])
def test_unusable_assets_do_not_open_device(make_service, tmp_path, invalid):
    if invalid == "malformed":
        (tmp_path / "move.wav").write_bytes(b"not a wave")
    elif invalid in ("different_format", "truncated"):
        (tmp_path / "move.wav").write_bytes((ASSETS / "move.wav").read_bytes())
        if invalid == "different_format":
            with wave.open(str(tmp_path / "capture.wav"), "wb") as audio:
                audio.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
                audio.writeframes(b"\0\0" * 20)
        else:
            data = (ASSETS / "capture.wav").read_bytes()
            (tmp_path / "capture.wav").write_bytes(data[:-20])
    service, backend = make_service(assets_dir=tmp_path)
    service.set_enabled(True)
    service.play()
    service.play(capture=True)
    assert backend.calls == []


def test_nonwindows_and_explicit_none_are_silent(monkeypatch):
    monkeypatch.setattr(sound.sys, "platform", "linux")
    for service in (MoveSounds(), MoveSounds(backend=None)):
        service.set_enabled(True)
        service.play()
        service.close()
        assert service._backend is None


def test_unavailable_native_library_is_nonfatal(monkeypatch):
    def unavailable(*args):
        raise OSError("WinMM unavailable")

    monkeypatch.setattr(sound.sys, "platform", "win32")
    monkeypatch.setattr(sound.ctypes, "WinDLL", unavailable, raising=False)
    service = MoveSounds()
    service.set_enabled(True)
    service.play()
    service.close()
    assert service._backend is None


def test_native_bindings_have_pointer_sized_handles_and_sdk_structure_layout(monkeypatch):
    functions = {name: SimpleNamespace() for name in (
        "waveOutOpen", "waveOutPrepareHeader", "waveOutWrite", "waveOutUnprepareHeader", "waveOutReset", "waveOutClose")}
    api = SimpleNamespace(**functions)
    monkeypatch.setattr(sound.sys, "platform", "win32")
    monkeypatch.setattr(sound.ctypes, "WinDLL", lambda name: api, raising=False)
    assert sound._load_backend() is api
    assert ctypes.sizeof(WAVEFORMATEX) == 18
    assert WAVEFORMATEX.cbSize.offset == 16
    assert ctypes.sizeof(WAVEHDR) == (48 if ctypes.sizeof(ctypes.c_void_p) == 8 else 32)
    assert WAVEHDR.dwUser.offset == (16 if ctypes.sizeof(ctypes.c_void_p) == 8 else 12)
    assert api.waveOutOpen.argtypes == [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
        ctypes.POINTER(WAVEFORMATEX), ctypes.c_size_t, ctypes.c_size_t, ctypes.c_uint32]
    for name in ("waveOutPrepareHeader", "waveOutWrite", "waveOutUnprepareHeader"):
        assert functions[name].argtypes == [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), ctypes.c_uint32]
    for name in ("waveOutReset", "waveOutClose"):
        assert functions[name].argtypes == [ctypes.c_void_p]
    assert all(function.restype is ctypes.c_uint32 for function in functions.values())
