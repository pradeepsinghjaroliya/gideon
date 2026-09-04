"""Loads config/config.yaml into typed dataclasses.

Each module should import only the dataclass for its own section, e.g.:

    from shared.config import load_config
    cfg = load_config()
    cfg.stt.model_size
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# The config file is looked up in this order, first hit wins:
#
#   1. an explicit `path=` passed to load_config()
#   2. $GIDEON_CONFIG
#   3. /etc/gideon/config.yaml        - installed by the .deb
#   4. repo_root/config/config.yaml   - running from a checkout
#
# 4 is resolved relative to this file so it works no matter which module's
# directory the caller runs from, but that only holds for an editable
# install: once `shared` is installed somewhere else (e.g. /opt/gideon/app)
# the walk up lands nowhere useful, which is why 3 exists. It stays last so
# a checkout keeps working even on a machine that also has the .deb
# installed.
SYSTEM_CONFIG_PATH = Path("/etc/gideon/config.yaml")


def _repo_config_path() -> Path | None:
    """repo_root/config/config.yaml, or None if this file isn't nested
    deeply enough for the walk up (i.e. we're not in a source checkout)."""
    here = Path(__file__).resolve()
    try:
        return here.parents[4] / "config" / "config.yaml"
    except IndexError:
        return None


REPO_CONFIG_PATH = _repo_config_path()

# Kept so anything importing this name directly still works.
DEFAULT_CONFIG_PATH = REPO_CONFIG_PATH


def resolve_config_path() -> Path:
    """Return the path load_config() would read, without reading it."""
    env_path = os.environ.get("GIDEON_CONFIG")
    if env_path:
        return Path(env_path)
    if SYSTEM_CONFIG_PATH.is_file():
        return SYSTEM_CONFIG_PATH
    if REPO_CONFIG_PATH is not None:
        return REPO_CONFIG_PATH
    return SYSTEM_CONFIG_PATH


class ConfigError(RuntimeError):
    """Raised when config/config.yaml is missing or malformed."""


@dataclass
class AudioConfig:
    input_device: str = "default"
    output_device: str = "default"
    sample_rate: int = 16000
    frame_ms: int = 30


@dataclass
class WakeWordConfig:
    backend: str = "openwakeword"
    model: str = "modules/02-wake-word/models/hey_gideon.onnx"
    threshold: float = 0.5


@dataclass
class SttConfig:
    backend: str = "faster_whisper"
    model_size: str = "small"
    device: str = "cpu"
    # Absolute path to an already-downloaded CTranslate2 model directory.
    # Set by the .deb so the engine never reaches Hugging Face; when None,
    # `model_size` is used and faster-whisper downloads on first use.
    model_path: str | None = None


@dataclass
class LlmConfig:
    backend: str = "ollama"
    model: str = "qwen2.5:1.5b"
    base_url: str = "http://localhost:11434"
    system_prompt: str = "You are a concise local voice assistant."


@dataclass
class TtsConfig:
    backend: str = "piper"
    voice: str = "en_US-lessac-high"
    # Directory holding <voice>.onnx / <voice>.onnx.json. Set by the .deb so
    # PiperEngine never downloads; when None it falls back to
    # ~/.cache/piper-voices and fetches the voice on first use.
    voices_dir: str | None = None


@dataclass
class TextInputConfig:
    hotkey: str | None = None


@dataclass
class OrchestratorConfig:
    history_turns: int = 6
    followup_seconds: float = 10.0


@dataclass
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    wake_word: WakeWordConfig = field(default_factory=WakeWordConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    text_input: TextInputConfig = field(default_factory=TextInputConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)


_SECTION_BUILDERS = {
    "audio": AudioConfig,
    "wake_word": WakeWordConfig,
    "stt": SttConfig,
    "llm": LlmConfig,
    "tts": TtsConfig,
    "text_input": TextInputConfig,
    "orchestrator": OrchestratorConfig,
}


def load_config(path: Path | str | None = None) -> Config:
    """Load config.yaml into a Config. Missing sections fall back to
    dataclass defaults; unknown top-level keys are ignored rather than
    erroring, so a module's not-yet-added section doesn't break loading.
    """
    config_path = Path(path) if path is not None else resolve_config_path()

    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a top-level mapping, got {type(raw).__name__}")

    sections = {}
    for key, builder in _SECTION_BUILDERS.items():
        section_data = raw.get(key) or {}
        if not isinstance(section_data, dict):
            raise ConfigError(f"config section '{key}' must be a mapping, got {type(section_data).__name__}")
        try:
            sections[key] = builder(**section_data)
        except TypeError as exc:
            raise ConfigError(f"invalid field in config section '{key}': {exc}") from exc

    return Config(**sections)
