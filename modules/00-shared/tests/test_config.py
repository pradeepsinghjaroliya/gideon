from pathlib import Path

import pytest

import shared.config as config_module
from shared.config import ConfigError, LlmConfig, load_config, set_transcript_ui_enabled
from shared.config import _apply_llm_env_overrides

REPO_ROOT_CONFIG = Path(__file__).resolve().parents[3] / "config" / "config.yaml"


def test_loads_seeded_repo_config():
    """Loads the real config.yaml via an explicit path - deliberately not
    `load_config()`'s default `path=None` - so this stays a deterministic
    check of the committed file's own contents, unaffected by whatever
    `.env` (GIDEON_LLM_MODEL) happens to exist on the machine running it."""
    cfg = load_config(REPO_ROOT_CONFIG)

    assert cfg.audio.sample_rate == 16000
    assert cfg.wake_word.backend == "openwakeword"
    assert cfg.stt.model_size == "small"
    assert cfg.llm.backend == "fireworks"
    assert cfg.llm.model == "accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b"
    assert cfg.llm.api_key_env == "FIREWORKS_API_KEY"
    assert cfg.tts.voice == "en_US-lessac-high"
    assert cfg.text_input.hotkey is None
    assert cfg.orchestrator.history_turns == 6


def test_apply_llm_env_overrides_sets_model_from_env(monkeypatch):
    monkeypatch.setenv("GIDEON_LLM_MODEL", "some/override-model:free")
    llm = LlmConfig(model="whatever-was-there")

    _apply_llm_env_overrides(llm)

    assert llm.model == "some/override-model:free"


def test_apply_llm_env_overrides_leaves_model_alone_when_unset(monkeypatch):
    monkeypatch.delenv("GIDEON_LLM_MODEL", raising=False)
    llm = LlmConfig(model="whatever-was-there")

    _apply_llm_env_overrides(llm)

    assert llm.model == "whatever-was-there"


def test_load_config_with_explicit_path_ignores_env_override(monkeypatch, tmp_path):
    """A developer's personal .env must never leak into a test (or any
    other caller) that points load_config at its own scratch file."""
    monkeypatch.setenv("GIDEON_LLM_MODEL", "should-not-leak:free")
    config = tmp_path / "config.yaml"
    config.write_text("llm:\n  model: from-the-yaml-file\n")

    cfg = load_config(config)

    assert cfg.llm.model == "from-the-yaml-file"


def test_load_config_default_path_applies_env_model_override(monkeypatch, tmp_path):
    """The `path=None` (real default) branch does load `.env` and apply
    its override - proven here against a monkeypatched `DEFAULT_ENV_PATH`/
    `DEFAULT_CONFIG_PATH` so it never touches this machine's real `.env`."""
    monkeypatch.delenv("GIDEON_LLM_MODEL", raising=False)
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", REPO_ROOT_CONFIG)
    env_file = tmp_path / ".env"
    env_file.write_text("GIDEON_LLM_MODEL=test/override-proof:free\n")
    monkeypatch.setattr(config_module, "DEFAULT_ENV_PATH", env_file)

    cfg = load_config()

    assert cfg.llm.model == "test/override-proof:free"


def test_load_config_default_path_tolerates_missing_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("GIDEON_LLM_MODEL", raising=False)
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", REPO_ROOT_CONFIG)
    monkeypatch.setattr(config_module, "DEFAULT_ENV_PATH", tmp_path / "does_not_exist.env")

    cfg = load_config()  # must not raise

    assert cfg.llm.model == "accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b"  # config.yaml's own value, unoverridden


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
    assert cfg.llm.model == "llama3.2:3b"


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
