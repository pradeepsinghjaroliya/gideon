import subprocess

import pytest

from agentic.tools.system_volume_tool import (
    SystemVolumeError,
    _run_wpctl,
    get_system_volume,
    set_system_volume,
)


def _fake_run(*, stdout="Volume: 0.50\n", fail=False):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if fail:
            raise subprocess.CalledProcessError(1, argv)
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    run.calls = calls
    return run


def test_get_system_volume_parses_percentage(monkeypatch):
    run = _fake_run(stdout="Volume: 0.50\n")
    monkeypatch.setattr(
        "agentic.tools.system_volume_tool._run_wpctl",
        lambda args: _run_wpctl(args, run=run),
    )

    assert get_system_volume() == 50
    assert run.calls == [["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]]


def test_get_system_volume_returns_zero_when_muted(monkeypatch):
    run = _fake_run(stdout="Volume: 0.75 [MUTED]\n")
    monkeypatch.setattr(
        "agentic.tools.system_volume_tool._run_wpctl",
        lambda args: _run_wpctl(args, run=run),
    )

    assert get_system_volume() == 0


def test_get_system_volume_raises_on_unparseable_output(monkeypatch):
    run = _fake_run(stdout="garbage\n")
    monkeypatch.setattr(
        "agentic.tools.system_volume_tool._run_wpctl",
        lambda args: _run_wpctl(args, run=run),
    )

    with pytest.raises(SystemVolumeError, match="could not parse"):
        get_system_volume()


@pytest.mark.parametrize("percent", [-1, 101, 1000])
def test_set_system_volume_rejects_out_of_range_percent(percent, monkeypatch):
    calls = []
    monkeypatch.setattr("agentic.tools.system_volume_tool._run_wpctl", lambda args: calls.append(args))

    with pytest.raises(SystemVolumeError, match="between 0 and 100"):
        set_system_volume(percent)

    assert calls == []


def test_set_system_volume_calls_set_volume_then_unmute(monkeypatch):
    calls = []
    monkeypatch.setattr("agentic.tools.system_volume_tool._run_wpctl", lambda args: calls.append(args))

    result = set_system_volume(40)

    assert calls == [
        ["set-volume", "@DEFAULT_AUDIO_SINK@", "40%"],
        ["set-mute", "@DEFAULT_AUDIO_SINK@", "0"],
    ]
    assert result == "System volume set to 40%."


def test_run_wpctl_wraps_subprocess_failure():
    run = _fake_run(fail=True)

    with pytest.raises(SystemVolumeError, match="wpctl get-volume .* failed"):
        _run_wpctl(["get-volume", "@DEFAULT_AUDIO_SINK@"], run=run)
