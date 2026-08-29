"""Microphone capture implementing `shared.interfaces.AudioSource`.

Mute contract (see plan.md "Open decisions"): while `muted` is True,
`read_chunk()` still drains real frames from the mic (so the callback's
internal queue can't back up and stall capture) but returns a same-shaped
array of zeros instead of the real samples. Callers never see raw audio
while the source is muted.
"""

from __future__ import annotations

import queue

import numpy as np

from audio_io.devices import resolve_device


def _default_stream_factory(**kwargs):
    import sounddevice as sd

    return sd.InputStream(**kwargs)


class MicAudioSource:
    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 30,
        device: str | int | None = "default",
        stream_factory=None,
    ) -> None:
        self._sample_rate = sample_rate
        self._frame_samples = int(sample_rate * frame_ms / 1000)
        self._device = resolve_device(device)
        self._stream_factory = stream_factory or _default_stream_factory
        self._stream = None
        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self.muted = False

    def _callback(self, indata, frames, time_info, status) -> None:
        self._queue.put(np.asarray(indata)[:, 0].copy())

    def start(self) -> None:
        self._stream = self._stream_factory(
            samplerate=self._sample_rate,
            channels=1,
            dtype="int16",
            device=self._device,
            blocksize=self._frame_samples,
            callback=self._callback,
        )
        self._stream.start()

    def read_chunk(self) -> np.ndarray:
        chunk = self._queue.get()
        if self.muted:
            return np.zeros_like(chunk)
        return chunk

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
