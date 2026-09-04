"""Speech-to-text implementing `shared.interfaces.STTEngine`.

Wraps faster-whisper. `transcribe()` takes the shared int16 mono
`np.ndarray` audio convention, converts to the float32 [-1, 1] range
faster-whisper expects, runs the model, and concatenates segment texts
into a single stripped string.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

# (audio_float32, sample_rate) -> transcript text
ModelFn = Callable[[np.ndarray, int], str]


class _FasterWhisperModel:
    """Adapts faster-whisper's `WhisperModel` (segment-generator API) to
    the single-string callable this module works with internally."""

    def __init__(self, model_size: str, device: str, model_path: str | None = None) -> None:
        from faster_whisper import WhisperModel

        # int8 keeps CPU inference fast without a separate GPU compute
        # type to pick; faster-whisper falls back to float32 on CPU if
        # int8 isn't supported for the given model/device combination.
        compute_type = "int8" if device == "cpu" else "float16"

        # WhisperModel takes a size name ("small") or a CTranslate2 model
        # directory through the same argument - it branches on
        # os.path.isdir(). A size name means it downloads from Hugging Face
        # on first use, which an offline install can't do, so a configured
        # model_path wins. local_files_only makes a missing or incomplete
        # directory fail immediately instead of silently falling back to a
        # ~500MB download.
        if model_path:
            self._model = WhisperModel(
                model_path, device=device, compute_type=compute_type, local_files_only=True
            )
        else:
            self._model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def __call__(self, audio_float32: np.ndarray, sample_rate: int) -> str:
        segments, _info = self._model.transcribe(audio_float32)
        return "".join(segment.text for segment in segments)


def _load_default_model(model_size: str, device: str, model_path: str | None = None) -> _FasterWhisperModel:
    return _FasterWhisperModel(model_size, device, model_path)


class FasterWhisperEngine:
    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        model_path: str | None = None,
        model_fn: ModelFn | None = None,
    ) -> None:
        self._model_fn = model_fn or _load_default_model(model_size, device, model_path)

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        audio_float32 = audio.astype(np.float32) / 32768.0
        text = self._model_fn(audio_float32, sample_rate)
        return text.strip()
