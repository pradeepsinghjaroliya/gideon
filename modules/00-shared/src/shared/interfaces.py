"""Shared interface contracts for every gideon module.

These are structural (typing.Protocol) contracts, not base classes -
implementations do not need to subclass anything, they just need to match
the method signatures. See ARCHITECTURE.md for the full pipeline context.

Audio convention: unless documented otherwise, audio arrays are mono
int16 numpy arrays at 16kHz.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class AudioSource(Protocol):
    def start(self) -> None: ...

    def read_chunk(self) -> np.ndarray: ...

    def stop(self) -> None: ...


@runtime_checkable
class AudioSink(Protocol):
    def play(self, audio: np.ndarray, sample_rate: int) -> None: ...

    def stop(self) -> None: ...


@runtime_checkable
class VoiceActivityDetector(Protocol):
    def is_speech(self, chunk: np.ndarray) -> bool: ...


@runtime_checkable
class WakeWordDetector(Protocol):
    def process_chunk(self, chunk: np.ndarray) -> bool: ...

    def reset(self) -> None: ...


@runtime_checkable
class STTEngine(Protocol):
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str: ...


@runtime_checkable
class LLMClient(Protocol):
    def generate(self, prompt: str, history: list[dict]) -> str: ...


@runtime_checkable
class TTSEngine(Protocol):
    def synthesize(self, text: str) -> tuple[np.ndarray, int]: ...


@runtime_checkable
class TextInputProvider(Protocol):
    def get_text(self) -> str | None: ...
