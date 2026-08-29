"""Silero VAD wrapper implementing `shared.interfaces.VoiceActivityDetector`.

Silero's model only accepts fixed-size windows (512 samples at 16kHz, 256
at 8kHz) and is stateful across calls, so this class buffers whatever
chunk size `is_speech()` is fed, runs the model once per full window, and
applies a silence "hangover" so a brief dip in speech probability
mid-utterance doesn't flip `is_speech()` to False prematurely.

Silence hangover chosen: 800ms (see plan.md "Open decisions").
"""

from __future__ import annotations

from typing import Callable

import numpy as np

_WINDOW_SAMPLES = {16000: 512, 8000: 256}

ModelFn = Callable[[np.ndarray, int], float]


def _load_default_model() -> ModelFn:
    import torch
    from silero_vad import load_silero_vad

    model = load_silero_vad(onnx=True)

    def predict(window: np.ndarray, sample_rate: int) -> float:
        audio_float = window.astype(np.float32) / 32768.0
        tensor = torch.from_numpy(audio_float).unsqueeze(0)
        prob = model(tensor, sample_rate)
        return float(prob.item())

    return predict


class SileroVAD:
    def __init__(
        self,
        sample_rate: int = 16000,
        threshold: float = 0.5,
        silence_duration_ms: int = 800,
        model: ModelFn | None = None,
    ) -> None:
        if sample_rate not in _WINDOW_SAMPLES:
            raise ValueError(f"unsupported sample_rate for Silero VAD: {sample_rate}")
        self._sample_rate = sample_rate
        self._window_samples = _WINDOW_SAMPLES[sample_rate]
        self._threshold = threshold
        self._silence_samples_needed = int(sample_rate * silence_duration_ms / 1000)
        self._predict = model if model is not None else _load_default_model()
        self._buffer = np.empty(0, dtype=np.int16)
        self._silent_run = 0
        self._speaking = False

    def is_speech(self, chunk: np.ndarray) -> bool:
        self._buffer = np.concatenate([self._buffer, chunk])
        while len(self._buffer) >= self._window_samples:
            window = self._buffer[: self._window_samples]
            self._buffer = self._buffer[self._window_samples :]
            prob = self._predict(window, self._sample_rate)
            if prob >= self._threshold:
                self._speaking = True
                self._silent_run = 0
            else:
                self._silent_run += self._window_samples
                if self._silent_run >= self._silence_samples_needed:
                    self._speaking = False
        return self._speaking

    def reset(self) -> None:
        self._buffer = np.empty(0, dtype=np.int16)
        self._silent_run = 0
        self._speaking = False
