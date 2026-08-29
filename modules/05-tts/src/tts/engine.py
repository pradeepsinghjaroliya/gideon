"""Text-to-speech implementing `shared.interfaces.TTSEngine`.

Wraps Piper. `synthesize()` returns the shared int16 mono `np.ndarray` audio
convention at the voice's native sample rate - resampling for playback is
`01-audio-io`'s job, not this module's (see `../../ARCHITECTURE.md`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

# text -> (int16 mono audio, sample_rate)
SynthFn = Callable[[str], "tuple[np.ndarray, int]"]

DEFAULT_VOICES_DIR = Path.home() / ".cache" / "piper-voices"

# Piper defaults to zero gap between sentence chunks; a real multi-sentence
# reply sounds run-on without one, so this module adds a small fixed gap.
_SENTENCE_GAP_SECONDS = 0.2


class _PiperModel:
    """Adapts Piper's `PiperVoice` (chunk-generator API) to the single
    (audio, sample_rate) callable this module works with internally.
    Downloads the voice's model files on first use if they're missing."""

    def __init__(self, voice: str, voices_dir: Path) -> None:
        from piper import PiperVoice
        from piper.download_voices import download_voice

        model_path = voices_dir / f"{voice}.onnx"
        if not model_path.exists():
            voices_dir.mkdir(parents=True, exist_ok=True)
            download_voice(voice, voices_dir)

        self._voice = PiperVoice.load(model_path)

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        chunks = list(self._voice.synthesize(text))
        if not chunks:
            return np.zeros(0, dtype=np.int16), self._voice.config.sample_rate

        sample_rate = chunks[0].sample_rate
        gap = np.zeros(int(sample_rate * _SENTENCE_GAP_SECONDS), dtype=np.int16)

        pieces: list[np.ndarray] = []
        for i, chunk in enumerate(chunks):
            if i > 0:
                pieces.append(gap)
            pieces.append(chunk.audio_int16_array)

        return np.concatenate(pieces), sample_rate


def _load_default_synth_fn(voice: str, voices_dir: Path) -> SynthFn:
    return _PiperModel(voice, voices_dir)


class PiperEngine:
    def __init__(
        self,
        voice: str = "en_US-lessac-medium",
        voices_dir: Path | str | None = None,
        synth_fn: SynthFn | None = None,
    ) -> None:
        resolved_dir = Path(voices_dir) if voices_dir is not None else DEFAULT_VOICES_DIR
        self._synth_fn = synth_fn or _load_default_synth_fn(voice, resolved_dir)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        return self._synth_fn(text)
