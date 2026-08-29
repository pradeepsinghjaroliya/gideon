from pathlib import Path

import pytest

from shared.config import ConfigError, load_config

REPO_ROOT_CONFIG = Path(__file__).resolve().parents[3] / "config" / "config.yaml"


def test_loads_seeded_repo_config():
    cfg = load_config(REPO_ROOT_CONFIG)

    assert cfg.audio.sample_rate == 16000
    assert cfg.wake_word.backend == "openwakeword"
    assert cfg.stt.model_size == "small"
    assert cfg.llm.model == "qwen2.5:1.5b"
    assert cfg.tts.voice == "en_US-lessac-high"
    assert cfg.text_input.hotkey is None
    assert cfg.orchestrator.history_turns == 6


def test_missing_file_raises_clear_error(tmp_path):
    missing = tmp_path / "does_not_exist.yaml"

    with pytest.raises(ConfigError, match="not found"):
        load_config(missing)


def test_malformed_yaml_raises_clear_error(tmp_path):
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text("audio: [this is not: a valid mapping")

    with pytest.raises(ConfigError, match="failed to parse"):
        load_config(bad_file)


def test_partial_config_falls_back_to_defaults(tmp_path):
    partial = tmp_path / "partial.yaml"
    partial.write_text("stt:\n  model_size: tiny\n")

    cfg = load_config(partial)

    assert cfg.stt.model_size == "tiny"
    # untouched sections keep their dataclass defaults
    assert cfg.audio.sample_rate == 16000
    assert cfg.llm.model == "qwen2.5:1.5b"


def test_unknown_field_in_section_raises_clear_error(tmp_path):
    bad = tmp_path / "bad_field.yaml"
    bad.write_text("stt:\n  not_a_real_field: 123\n")

    with pytest.raises(ConfigError, match="invalid field"):
        load_config(bad)
