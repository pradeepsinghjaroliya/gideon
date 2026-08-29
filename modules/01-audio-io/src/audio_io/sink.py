"""Speaker playback implementing `shared.interfaces.AudioSink`.

TTS engines produce audio at their own native rate (Piper: commonly
22050 Hz); per ARCHITECTURE.md this module resamples to the output
device's native rate before playback, so producers don't need to care.
"""

from __future__ import annotations

import numpy as np

from audio_io.devices import resolve_device


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
    ) -> None:
        self._device = resolve_device(device)
        self._stream_factory = stream_factory or _default_output_stream_factory
        self._stream = None

    def _device_native_rate(self) -> int:
        import sounddevice as sd

        info = sd.query_devices(self._device, "output")
        return int(info["default_samplerate"])

    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        target_rate = self._device_native_rate()
        audio = resample(audio, sample_rate, target_rate)

        self._stream = self._stream_factory(
            samplerate=target_rate,
            channels=1,
            dtype=audio.dtype,
            device=self._device,
        )
        self._stream.start()
        try:
            self._stream.write(audio)
        finally:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.abort()
            self._stream.close()
            self._stream = None
