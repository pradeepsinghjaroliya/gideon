"""Microphone capture implementing `shared.interfaces.AudioSource`.

Mute contract (see plan.md "Open decisions"): while `muted` is True,
`read_chunk()` still drains real frames from the mic (so the callback's
internal queue can't back up and stall capture) but returns a same-shaped
array of zeros instead of the real samples. Callers never see raw audio
while the source is muted.
"""

from __future__ import annotations

import logging
import queue

import numpy as np

from audio_io.devices import resolve_device

_READ_TIMEOUT_SECONDS = 1.0


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
        logger: logging.Logger | None = None,
        read_timeout: float = _READ_TIMEOUT_SECONDS,
    ) -> None:
        self._sample_rate = sample_rate
        self._frame_samples = int(sample_rate * frame_ms / 1000)
        self._device = resolve_device(device)
        self._stream_factory = stream_factory or _default_stream_factory
        self._stream = None
        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._log = logger or logging.getLogger("orchestrator")
        self._read_timeout = read_timeout
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
        """A real confirmed failure mode: aborting the playback stream
        mid-write (the dashboard's "Stop speaking") can, on some
        PortAudio/ALSA/PulseAudio backends, hiccup the *input* stream's
        callback too - it just silently stops firing. `queue.Queue.get()`
        has no timeout, so that used to block this method forever, which
        in turn hung `_drain_context()`'s `thread.join()` in
        `07-orchestrator/state_machine.py` and froze the whole
        orchestrator thread with no exception and no log line - a real,
        reported "silent crash" (the tray/dashboard thread, being
        unrelated, kept working fine, which is what made it look like a
        selective hang rather than a full crash).

        Bounded by `_READ_TIMEOUT_SECONDS` instead: a timeout returns a
        silent (all-zero) frame - the same shape/dtype a real frame would
        have, and the same value a genuine mute already produces below -
        so callers never need to special-case it, and a warning is logged
        so a real stall is visible instead of invisible. This can't lose
        real audio (a live callback firing normally will always win the
        race against a 1s timeout, which is over 30x this module's
        default 30ms frame interval), and it keeps the orchestrator loop
        alive (able to keep noticing typed "Ask..." input, `set_online`,
        etc.) even if the mic hardware itself needs a restart to recover.
        """
        try:
            chunk = self._queue.get(timeout=self._read_timeout)
        except queue.Empty:
            self._log.warning(
                "mic queue produced no frame for %.0fs - returning silence "
                "(input stream may have stalled; audio may need reconnecting)",
                self._read_timeout,
            )
            return np.zeros(self._frame_samples, dtype=np.int16)
        if self.muted:
            return np.zeros_like(chunk)
        return chunk

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
