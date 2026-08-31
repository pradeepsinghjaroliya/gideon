"""Wake word detection implementing `shared.interfaces.WakeWordDetector`.

Wraps openWakeWord. `process_chunk()` is edge-triggered: it returns True
only on the transition from below-threshold to at-or-above-threshold, and
re-arms automatically once the score drops back below threshold (so it
won't fire on every single frame of a sustained "hey jarvis"). `reset()`
is an explicit re-arm for callers that want a hard reset regardless of
the current score - e.g. the orchestrator, after handling a detection and
returning to IDLE.
"""

from __future__ import annotations

import os
from typing import Callable

import numpy as np

ModelFn = Callable[[np.ndarray], float]


class _OpenWakeWordModel:
    """Adapts openWakeWord's `Model` (multi-model, dict-of-scores API) to
    the single-score callable this module works with internally."""

    def __init__(self, model_name: str) -> None:
        from openwakeword.model import Model

        # openwakeword.Model keys its predictions dict by
        # os.path.splitext(os.path.basename(path))[0] for a custom model
        # path (e.g. "models/hey_gideon.onnx" -> "hey_gideon"), not by the
        # raw path string - this is a no-op for a bare built-in name like
        # "hey_jarvis", so it works for both cases.
        self._model_name = os.path.splitext(os.path.basename(model_name))[0]
        # Force the ONNX backend: openwakeword defaults to tflite when
        # tflite-runtime is installed, but tflite-runtime's compiled
        # extension is built against the NumPy 1.x ABI and crashes under
        # NumPy 2.x. onnxruntime has no such issue.
        self._model = Model(wakeword_models=[model_name], inference_framework="onnx")

    def __call__(self, chunk: np.ndarray) -> float:
        predictions = self._model.predict(chunk)
        return float(predictions[self._model_name])

    def reset(self) -> None:
        reset_fn = getattr(self._model, "reset", None)
        if callable(reset_fn):
            reset_fn()


def _load_default_model(model_name: str) -> _OpenWakeWordModel:
    return _OpenWakeWordModel(model_name)


class OpenWakeWordDetector:
    def __init__(
        self,
        model: str = "modules/02-wake-word/models/hey_gideon.onnx",
        threshold: float = 0.5,
        model_fn: ModelFn | None = None,
    ) -> None:
        self._threshold = threshold
        self._model_fn = model_fn or _load_default_model(model)
        self._armed = True

    def process_chunk(self, chunk: np.ndarray) -> bool:
        score = self._model_fn(chunk)
        if score >= self._threshold:
            if self._armed:
                self._armed = False
                return True
            return False
        self._armed = True
        return False

    def reset(self) -> None:
        self._armed = True
        reset_fn = getattr(self._model_fn, "reset", None)
        if callable(reset_fn):
            reset_fn()
