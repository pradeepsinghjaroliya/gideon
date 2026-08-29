import pytest

from orchestrator.ollama_control import OllamaControl, OllamaControlError


def test_is_running_delegates_to_health_check_with_tags_endpoint():
    calls = []
    control = OllamaControl(base_url="http://localhost:11434", health_check=lambda url: calls.append(url) or True)

    assert control.is_running() is True
    assert calls == ["http://localhost:11434/api/tags"]


def test_is_running_false_when_health_check_fails():
    control = OllamaControl(health_check=lambda url: False)

    assert control.is_running() is False


def test_start_launches_ollama_serve_using_resolved_binary_path():
    calls = []
    control = OllamaControl(popen=lambda argv: calls.append(argv), which=lambda name: "/usr/local/bin/ollama")

    control.start()

    assert calls == [["/usr/local/bin/ollama", "serve"]]


def test_start_raises_clear_error_when_ollama_not_on_path():
    control = OllamaControl(which=lambda name: None)

    with pytest.raises(OllamaControlError, match="not found on PATH"):
        control.start()


def test_start_raises_clear_error_when_popen_fails():
    def failing_popen(argv):
        raise OSError("boom")

    control = OllamaControl(which=lambda name: "/usr/local/bin/ollama", popen=failing_popen)

    with pytest.raises(OllamaControlError, match="failed to start"):
        control.start()


def test_stop_kills_ollama_serve_by_name_not_pid():
    calls = []
    control = OllamaControl(run=lambda argv: calls.append(argv))

    control.stop()

    assert calls == [["pkill", "-f", "ollama serve"]]


def test_base_url_trailing_slash_is_stripped():
    calls = []
    control = OllamaControl(base_url="http://localhost:11434/", health_check=lambda url: calls.append(url) or True)

    control.is_running()

    assert calls == ["http://localhost:11434/api/tags"]
