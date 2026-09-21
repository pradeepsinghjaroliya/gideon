import subprocess

import pytest

from agentic.tools.brightness_tool import (
    BrightnessError,
    _active_session_id,
    _find_backlight_device,
    _set_via_logind,
    get_screen_brightness,
    set_screen_brightness,
)


def _make_backlight_device(tmp_path, *, brightness: int, max_brightness: int, name: str = "intel_backlight"):
    device = tmp_path / name
    device.mkdir()
    (device / "brightness").write_text(str(brightness))
    (device / "max_brightness").write_text(str(max_brightness))
    return device


LOGINCTL_ACTIVE_OUTPUT = "2 1001 pjaroliya seat0 tty2 active no -\n"


def _fake_run(*, list_sessions_output=LOGINCTL_ACTIVE_OUTPUT, fail_on=None):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if fail_on and argv[0] == fail_on:
            raise subprocess.CalledProcessError(1, argv)
        if argv[:2] == ["loginctl", "list-sessions"]:
            return subprocess.CompletedProcess(argv, 0, stdout=list_sessions_output, stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    run.calls = calls
    return run


def test_find_backlight_device_returns_first_device(tmp_path):
    _make_backlight_device(tmp_path, brightness=500, max_brightness=1000)

    device = _find_backlight_device(root=tmp_path)

    assert device.name == "intel_backlight"


def test_find_backlight_device_raises_when_empty(tmp_path):
    with pytest.raises(BrightnessError, match="no backlight device"):
        _find_backlight_device(root=tmp_path)


def test_find_backlight_device_raises_when_root_missing(tmp_path):
    with pytest.raises(BrightnessError, match="no backlight device"):
        _find_backlight_device(root=tmp_path / "does-not-exist")


def test_get_screen_brightness_computes_percentage(tmp_path, monkeypatch):
    _make_backlight_device(tmp_path, brightness=500, max_brightness=1000)
    monkeypatch.setattr("agentic.tools.brightness_tool.BACKLIGHT_ROOT", tmp_path)

    assert get_screen_brightness() == 50


def test_get_screen_brightness_rounds(tmp_path, monkeypatch):
    _make_backlight_device(tmp_path, brightness=1, max_brightness=3)
    monkeypatch.setattr("agentic.tools.brightness_tool.BACKLIGHT_ROOT", tmp_path)

    assert get_screen_brightness() == 33


@pytest.mark.parametrize("percent", [-1, 101, 1000])
def test_set_screen_brightness_rejects_out_of_range_percent(percent, monkeypatch):
    calls = []
    monkeypatch.setattr("agentic.tools.brightness_tool._set_via_logind", lambda *a, **k: calls.append(a))

    with pytest.raises(BrightnessError, match="between 0 and 100"):
        set_screen_brightness(percent)

    assert calls == []


def test_set_screen_brightness_computes_value_and_calls_logind(tmp_path, monkeypatch):
    _make_backlight_device(tmp_path, brightness=500, max_brightness=1000)
    monkeypatch.setattr("agentic.tools.brightness_tool.BACKLIGHT_ROOT", tmp_path)
    captured = {}

    def fake_set_via_logind(subsystem, device_name, value, *, run=None):
        captured.update(subsystem=subsystem, device_name=device_name, value=value)

    monkeypatch.setattr("agentic.tools.brightness_tool._set_via_logind", fake_set_via_logind)

    result = set_screen_brightness(30)

    assert captured == {"subsystem": "backlight", "device_name": "intel_backlight", "value": 300}
    assert result == "Screen brightness set to 30%."


def test_active_session_id_finds_active_row():
    run = _fake_run()

    assert _active_session_id(run=run) == "2"


def test_active_session_id_raises_when_none_active():
    run = _fake_run(list_sessions_output="3 1001 pjaroliya - 4455 manager - no -\n")

    with pytest.raises(BrightnessError, match="no active or seated user login session"):
        _active_session_id(run=run)


def test_active_session_id_falls_back_to_seated_user_session(monkeypatch):
    monkeypatch.setattr("os.getuid", lambda: 1000)
    run = _fake_run(
        list_sessions_output=(
            "2 1000 zen seat0 3312 user tty2 no -\n"
            "3 1000 zen - 4455 manager - no -\n"
        )
    )

    assert _active_session_id(run=run) == "2"


def test_active_session_id_raises_when_loginctl_fails():
    run = _fake_run(fail_on="loginctl")

    with pytest.raises(BrightnessError, match="failed to list login sessions"):
        _active_session_id(run=run)


def test_set_via_logind_calls_busctl_with_expected_args():
    run = _fake_run()

    _set_via_logind("backlight", "intel_backlight", 700, run=run)

    assert run.calls[0] == ["loginctl", "list-sessions", "--no-legend"]
    assert run.calls[1] == [
        "busctl",
        "call",
        "org.freedesktop.login1",
        "/org/freedesktop/login1/session/2",
        "org.freedesktop.login1.Session",
        "SetBrightness",
        "ssu",
        "backlight",
        "intel_backlight",
        "700",
    ]


def test_set_via_logind_wraps_busctl_failure():
    run = _fake_run(fail_on="busctl")

    with pytest.raises(BrightnessError, match="failed to set brightness via logind"):
        _set_via_logind("backlight", "intel_backlight", 700, run=run)
