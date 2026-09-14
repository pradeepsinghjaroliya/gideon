from pathlib import Path

import pytest

from shared.config import ConfigError, load_config, set_transcript_ui_enabled

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


def test_set_transcript_ui_enabled_flips_the_value_in_place(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "transcript_ui:\n"
        "  enabled: true\n"
        "  autostart: true          # orchestrator starts/stops the overlay process\n"
        "  hide_after_seconds: 4.0\n"
        "\n"
        "orchestrator:\n"
        "  history_turns: 6\n"
    )

    set_transcript_ui_enabled(False, path=config)

    assert load_config(config).transcript_ui.enabled is False


def test_set_transcript_ui_enabled_preserves_every_other_line_verbatim(tmp_path):
    """The whole point of a targeted text edit rather than a YAML
    re-dump: comments and formatting elsewhere in the file must survive
    byte-for-byte."""
    config = tmp_path / "config.yaml"
    original = (
        "transcript_ui:\n"
        "  enabled: true\n"
        "  autostart: true          # orchestrator starts/stops the overlay process\n"
        "  hide_after_seconds: 4.0\n"
        "\n"
        "orchestrator:\n"
        "  history_turns: 6   # comment on an unrelated section\n"
    )
    config.write_text(original)

    set_transcript_ui_enabled(False, path=config)

    updated = config.read_text()
    assert updated == original.replace("enabled: true", "enabled: false", 1)


def test_set_transcript_ui_enabled_round_trips_true_and_false(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("transcript_ui:\n  enabled: false\n")

    set_transcript_ui_enabled(True, path=config)
    assert load_config(config).transcript_ui.enabled is True

    set_transcript_ui_enabled(False, path=config)
    assert load_config(config).transcript_ui.enabled is False


def test_set_transcript_ui_enabled_on_the_real_repo_config_round_trips(tmp_path):
    """Guards against the seeded config.yaml drifting into a shape (e.g. a
    reordered/renamed `enabled` line) that this targeted edit can no
    longer find."""
    original = REPO_ROOT_CONFIG.read_text()
    scratch = tmp_path / "config.yaml"
    scratch.write_text(original)
    was_enabled = load_config(scratch).transcript_ui.enabled

    set_transcript_ui_enabled(not was_enabled, path=scratch)
    assert load_config(scratch).transcript_ui.enabled is not was_enabled

    set_transcript_ui_enabled(was_enabled, path=scratch)
    assert scratch.read_text() == original


def test_set_transcript_ui_enabled_missing_section_raises_clear_error(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("orchestrator:\n  history_turns: 6\n")

    with pytest.raises(ConfigError, match="transcript_ui"):
        set_transcript_ui_enabled(True, path=config)
