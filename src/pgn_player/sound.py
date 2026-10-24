"""Optional local PCM sounds that overlap without interrupting earlier moves."""

from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import wave


_AUTO_BACKEND = object()
WAVE_MAPPER = 0xFFFFFFFF
WAVE_FORMAT_QUERY = 0x0001
WHDR_DONE = 0x00000001
MAX_VOICES = 8


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", ctypes.c_uint16), ("nChannels", ctypes.c_uint16),
        ("nSamplesPerSec", ctypes.c_uint32), ("nAvgBytesPerSec", ctypes.c_uint32),
        ("nBlockAlign", ctypes.c_uint16), ("wBitsPerSample", ctypes.c_uint16),
        ("cbSize", ctypes.c_uint16),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p), ("dwBufferLength", ctypes.c_uint32),
        ("dwBytesRecorded", ctypes.c_uint32), ("dwUser", ctypes.c_size_t),
        ("dwFlags", ctypes.c_uint32), ("dwLoops", ctypes.c_uint32),
        ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t),
    ]


def pcm_format(rate: int, channels: int, sample_width: int) -> WAVEFORMATEX:
    alignment = channels * sample_width
    return WAVEFORMATEX(1, channels, rate, rate * alignment, alignment, sample_width * 8, 0)


def _load_backend():
    if sys.platform != "win32":
        return None
    try:
        api = ctypes.WinDLL("winmm")
        api.waveOutOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
            ctypes.POINTER(WAVEFORMATEX), ctypes.c_size_t, ctypes.c_size_t, ctypes.c_uint32]
        api.waveOutOpen.restype = ctypes.c_uint32
        for name in ("waveOutPrepareHeader", "waveOutWrite", "waveOutUnprepareHeader"):
            function = getattr(api, name)
            function.argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), ctypes.c_uint32]
            function.restype = ctypes.c_uint32
        for name in ("waveOutReset", "waveOutClose"):
            function = getattr(api, name)
            function.argtypes = [ctypes.c_void_p]
            function.restype = ctypes.c_uint32
        return api
    except (OSError, RuntimeError, AttributeError):
        return None


# Native playback retains pointers, not Python references. Keep each open
# output alive until close succeeds, even if a driver rejects cleanup or its
# owning widget is discarded. Normal stop/close removes it from this registry.
_OPEN_OUTPUTS = []


class _Buffer:
    def __init__(self, pcm: bytes):
        self.data = ctypes.create_string_buffer(pcm, len(pcm))
        self.header = WAVEHDR(lpData=ctypes.addressof(self.data), dwBufferLength=len(pcm))
        self.submitted = False


class _Output:
    """One main-thread-owned device; the Windows driver queues its buffers."""

    def __init__(self, api, format_: WAVEFORMATEX):
        self.api = api
        self.handle = ctypes.c_void_p()
        self.buffers = []
        result = api.waveOutOpen(ctypes.byref(self.handle), WAVE_MAPPER,
                                ctypes.byref(format_), 0, 0, 0)  # CALLBACK_NULL
        if result or not self.handle.value:
            raise RuntimeError("Optional audio output is unavailable.")
        _OPEN_OUTPUTS.append(self)

    def reap(self) -> None:
        retained = []
        for buffer in self.buffers:
            if not buffer.submitted or buffer.header.dwFlags & WHDR_DONE:
                try:
                    result = self.api.waveOutUnprepareHeader(
                        self.handle, ctypes.byref(buffer.header), ctypes.sizeof(WAVEHDR))
                except Exception:
                    result = -1
                if result == 0:
                    continue
            retained.append(buffer)
        self.buffers = retained

    @property
    def queued_bytes(self) -> int:
        return sum(buffer.header.dwBufferLength for buffer in self.buffers
                   if buffer.submitted and not buffer.header.dwFlags & WHDR_DONE)

    def play(self, pcm: bytes) -> bool | None:
        self.reap()
        buffer = _Buffer(pcm)
        if self.api.waveOutPrepareHeader(self.handle, ctypes.byref(buffer.header), ctypes.sizeof(WAVEHDR)):
            return False
        # Retain both allocations before submitting either pointer to Windows.
        self.buffers.append(buffer)
        buffer.submitted = True
        try:
            result = self.api.waveOutWrite(self.handle, ctypes.byref(buffer.header), ctypes.sizeof(WAVEHDR))
        except Exception:
            # The ownership of a call that did not return is uncertain. Reset
            # before releasing anything, but never retry an uncertain write.
            return None
        if result:
            buffer.submitted = False
            self.reap()
            return False
        return True

    def stop(self) -> bool:
        if not self.handle.value:
            return True
        try:
            self.api.waveOutReset(self.handle)
        except Exception:
            pass
        # Successful reset marks queued buffers DONE. Never assume a failed
        # reset/unprepare returned memory still owned by the native driver.
        self.reap()
        if self.buffers:
            return False
        try:
            result = self.api.waveOutClose(self.handle)
        except Exception:
            return False
        if result:
            return False
        self.handle = ctypes.c_void_p()
        _OPEN_OUTPUTS.remove(self)
        return True


class MoveSounds:
    """Play overlapping moves using at most eight lazily opened PCM outputs.

    Assets are decoded once on first use. Each request owns a distinct native
    header and PCM buffer until the driver finishes and unprepares it. Completed
    buffers are collected on the next request or stop; no callback/thread is
    needed. Idle outputs are reused; bursts that fill all voices queue on the
    least-loaded output without truncating any recording. ``backend`` accepts
    a WinMM-compatible fake for silent tests, and explicitly passing None
    disables audio on any platform.
    """

    def __init__(self, *, backend=_AUTO_BACKEND, assets_dir: str | Path | None = None):
        self._backend = _load_backend() if backend is _AUTO_BACKEND else backend
        self._assets_dir = Path(assets_dir) if assets_dir is not None else Path(__file__).resolve().parent / "assets"
        self._enabled = False
        self._assets_loaded = False
        self._sounds = {}
        self._format = None
        self._outputs = []
        self._retired = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self.stop()

    def _load_assets(self) -> None:
        if self._assets_loaded:
            return
        self._assets_loaded = True
        sounds = {}
        common = None
        for capture, name in ((False, "move.wav"), (True, "capture.wav")):
            with wave.open(str(self._assets_dir / name), "rb") as audio:
                format_ = (audio.getframerate(), audio.getnchannels(), audio.getsampwidth())
                frames = audio.getnframes()
                pcm = audio.readframes(frames)
                if (audio.getcomptype() != "NONE" or format_[0] <= 0 or format_[1] not in (1, 2)
                        or format_[2] not in (1, 2) or not pcm
                        or len(pcm) != frames * format_[1] * format_[2]
                        or common is not None and common != format_):
                    raise ValueError("Unsupported bundled sound format.")
                common = format_
                sounds[capture] = pcm
        self._format = pcm_format(*common)
        self._sounds = sounds

    def play(self, *, capture: bool = False) -> None:
        if not self._enabled or self._backend is None:
            return
        output = None
        try:
            self._cleanup_retired()
            self._load_assets()
            pcm = self._sounds.get(bool(capture))
            if pcm is None:
                return
            for _ in range(2):
                output = self._select_output()
                accepted = output.play(pcm)
                if accepted is True:
                    return
                self._retire_output(output)
                output = None
                if accepted is None:
                    return
                # A rejected prepare/write may reflect a stale output device.
                # Retry once on another output, never replaying an accepted or
                # uncertain submission. Failed cleanup keeps its old pointers
                # alive without making that stale handle the active output.
        except Exception:
            # Optional audio must never interrupt chess navigation or emit a
            # fallback system alert when an asset/device is unavailable.
            if output is not None:
                self._retire_output(output)

    def _select_output(self) -> _Output:
        for output in self._outputs:
            output.reap()
        for output in self._outputs:
            if not output.buffers:
                return output
        if len(self._outputs) < MAX_VOICES:
            try:
                output = _Output(self._backend, self._format)
            except Exception:
                # A device may support fewer simultaneous outputs. Its
                # existing voices can still queue complete recordings.
                if not self._outputs:
                    raise
            else:
                self._outputs.append(output)
                return output
        return min(self._outputs, key=lambda output: output.queued_bytes)

    def _retire_output(self, output: _Output) -> None:
        self._outputs.remove(output)
        if not output.stop():
            self._retired.append(output)

    def _cleanup_retired(self) -> None:
        self._retired = [output for output in self._retired if not output.stop()]

    def stop(self) -> None:
        self._cleanup_retired()
        for output in list(self._outputs):
            self._retire_output(output)

    def close(self) -> None:
        self._enabled = False
        self.stop()
