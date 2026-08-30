"""Speaker playback implementing `shared.interfaces.AudioSink`.

TTS engines produce audio at their own native rate (Piper: commonly
22050 Hz); per ARCHITECTURE.md this module resamples to the output
device's native rate before playback, so producers don't need to care.
"""

from __future__ import annotations

import threading

import numpy as np

from audio_io.devices import resolve_device

_WRITE_CHUNK_SECONDS = 0.1


def resample(audio: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
    if orig_rate == target_rate or len(audio) == 0:
        return audio
    orig_len = len(audio)
    target_len = max(1, round(orig_len * target_rate / orig_rate))
    orig_x = np.arange(orig_len)
    target_x = np.linspace(0, orig_len - 1, num=target_len)
    resampled = np.interp(target_x, orig_x, audio.astype(np.float64))
    return resampled.astype(audio.dtype)


def _default_output_stream_factory(**kwargs):
    import sounddevice as sd

    return sd.OutputStream(**kwargs)


class SpeakerAudioSink:
    def __init__(
        self,
        device: str | int | None = "default",
        stream_factory=None,
        write_chunk_seconds: float = _WRITE_CHUNK_SECONDS,
    ) -> None:
        self._device = resolve_device(device)
        self._stream_factory = stream_factory or _default_output_stream_factory
        self._write_chunk_seconds = write_chunk_seconds
        self._stream = None
        self._stop_event = threading.Event()

    def _device_native_rate(self) -> int:
        import sounddevice as sd

        info = sd.query_devices(self._device, "output")
        return int(info["default_samplerate"])

    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        """Writes in small chunks (`_write_chunk_seconds` each) instead of
        one blocking `write()` call for the whole clip, checking
        `stop()`'s flag between chunks - a real, confirmed problem with
        the previous "one big `write()`, interrupt it with `abort()` from
        another thread" design: `abort()` mid-write triggered this
        machine's ALSA backend's own internal xrun-recovery path (visible
        as `Expression '...' failed in 'pa_linux_alsa.c'` lines printed
        straight to stderr by the PortAudio C library, bypassing Python
        entirely), which then spent several seconds failing and retrying
        before the interrupted `write()` finally raised. Small chunks
        checked between writes give the same "stop within a fraction of a
        second" responsiveness (default 100ms, negligible) without ever
        needing to abort an in-progress write. `self._stream` is now only
        ever touched by this method's own thread - `stop()` only sets a
        plain `threading.Event`, so this sink no longer has two threads
        touching the same PortAudio stream at all (the earlier crash and
        the ALSA xrun issue were both exactly that class of problem).
        """
        self._stop_event.clear()
        target_rate = self._device_native_rate()
        audio = resample(audio, sample_rate, target_rate)
        chunk_size = max(1, int(target_rate * self._write_chunk_seconds))

        stream = self._stream_factory(
            samplerate=target_rate,
            channels=1,
            dtype=audio.dtype,
            device=self._device,
        )
        self._stream = stream
        stream.start()
        try:
            for start in range(0, len(audio), chunk_size):
                if self._stop_event.is_set():
                    break
                stream.write(audio[start : start + chunk_size])
        finally:
            stream.stop()
            stream.close()
            if self._stream is stream:
                self._stream = None

    def stop(self) -> None:
        """Safe to call from another thread while `play()` runs on its
        own - see `play()`'s docstring for why this only ever sets a flag
        instead of reaching into the PortAudio stream itself."""
        self._stop_event.set()
