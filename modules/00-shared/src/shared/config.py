"""Loads config/config.yaml into typed dataclasses.

Each module should import only the dataclass for its own section, e.g.:

    from shared.config import load_config
    cfg = load_config()
    cfg.stt.model_size
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

# repo_root/config/config.yaml, resolved relative to this file so it works
# no matter which module's directory the caller runs from.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "config.yaml"
# repo_root/.env - personal secrets/overrides (see .env.example), gitignored.
# A no-op when missing, e.g. a checkout that hasn't copied .env.example yet.
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[4] / ".env"


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
    # Live partial transcription, for the transcript overlay - a second,
    # deliberately smaller model that re-transcribes speech while it is
    # still being spoken, so words can appear on screen before the main
    # `model_size` model has finished with the completed utterance. See
    # `03-stt/src/stt/streaming.py`. Set `partials: false` to turn the
    # feature (and its CPU cost) off entirely.
    partials: bool = True
    partial_model_size: str = "tiny"


@dataclass
class LlmConfig:
    # Provider id - a key into `agentic.providers.registry.PROVIDERS`
    # (09-agentic), not just an Ollama-specific label anymore.
    backend: str = "ollama"
    model: str = "llama3.2:3b"
    base_url: str = "http://localhost:11434"  # used by local providers (ollama)
    system_prompt: str = "You are a concise local voice assistant."
    # Name of an env var to read an API key from, for a remote provider -
    # never the key itself, so it never ends up in config.yaml/git.
    api_key_env: str = ""


@dataclass
class TtsConfig:
    backend: str = "piper"
    voice: str = "en_US-lessac-high"


@dataclass
class TextInputConfig:
    hotkey: str | None = None


@dataclass
class TranscriptUiConfig:
    """The on-screen conversation transcript overlay (`08-transcript-ui`).

    `socket_path: None` means "work it out" - `$XDG_RUNTIME_DIR` if it
    exists, a uid-qualified path under the temp dir otherwise (see
    `transcript_ui/protocol.py`). Left as a knob mainly so two instances
    can run side by side while developing.
    """

    enabled: bool = True
    # Have the orchestrator start and stop the overlay process itself.
    # Turn this off to run `python -m transcript_ui` by hand (or from a
    # user systemd unit) while still feeding it from the orchestrator.
    autostart: bool = True
    socket_path: str | None = None
    width: int = 720
    bottom_margin: int = 48
    max_turns: int = 4
    typewriter_cps: float = 55.0
    hide_after_seconds: float = 4.0


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
    transcript_ui: TranscriptUiConfig = field(default_factory=TranscriptUiConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)


_SECTION_BUILDERS = {
    "audio": AudioConfig,
    "wake_word": WakeWordConfig,
    "stt": SttConfig,
    "llm": LlmConfig,
    "tts": TtsConfig,
    "text_input": TextInputConfig,
    "transcript_ui": TranscriptUiConfig,
    "orchestrator": OrchestratorConfig,
}


def _apply_llm_env_overrides(llm: LlmConfig) -> None:
    """`GIDEON_LLM_MODEL`, if set, overrides `llm.model` - lets a personal
    model pick (or one tied to a specific API key/quota) live in `.env`
    instead of the committed `config.yaml` (see `.env.example`)."""
    model_override = os.environ.get("GIDEON_LLM_MODEL")
    if model_override:
        llm.model = model_override


def load_config(path: Path | str | None = None) -> Config:
    """Load config.yaml into a Config. Missing sections fall back to
    dataclass defaults; unknown top-level keys are ignored rather than
    erroring, so a module's not-yet-added section doesn't break loading.

    `path=None` (the real default, used by every entry point) also loads
    `.env` (a no-op if it doesn't exist) and applies its overrides - see
    `_apply_llm_env_overrides`. An explicit `path` (every test in this
    file bar `test_loads_seeded_repo_config`) skips both, so a developer's
    personal `.env` can never affect a test pointed at its own scratch
    config file.
    """
    use_env_overrides = path is None
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if use_env_overrides:
        load_dotenv(DEFAULT_ENV_PATH)

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

    config = Config(**sections)
    if use_env_overrides:
        _apply_llm_env_overrides(config.llm)
    return config


def set_transcript_ui_enabled(value: bool, path: Path | str | None = None) -> None:
    """Persists the tray's "Transcript" toggle so it survives a restart.

    Patches only the `transcript_ui.enabled` line in place, via a plain
    text edit rather than a `yaml.safe_load`/`safe_dump` round-trip -
    re-serializing the whole file through PyYAML would silently drop every
    inline `# ...` comment in config.yaml (see `autostart`,
    `hide_after_seconds`, etc.), which is not an acceptable side effect of
    flipping one boolean from a tray click.
    """
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    lines = config_path.read_text().splitlines(keepends=True)

    section_start = next(
        (i for i, line in enumerate(lines) if line.startswith("transcript_ui:")), None
    )
    if section_start is None:
        raise ConfigError(f"no 'transcript_ui' section found in {config_path}")

    for i in range(section_start + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line[0].isspace():
            break  # ran into the next top-level section without finding it
        # Strip the line ending before matching: `\s*` would otherwise
        # happily swallow it too (`\s` includes `\n`), and re-adding a
        # newline unconditionally below would then double it up.
        body, ending = (line[:-1], "\n") if line.endswith("\n") else (line, "")
        match = re.match(r"^(\s*enabled:\s*)(true|false)(\s*(?:#.*)?)$", body)
        if match:
            lines[i] = f"{match.group(1)}{'true' if value else 'false'}{match.group(3)}{ending}"
            config_path.write_text("".join(lines))
            return

    raise ConfigError(f"no 'transcript_ui.enabled' line found in {config_path}")
