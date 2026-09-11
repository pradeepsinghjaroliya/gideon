import subprocess
import sys
import time

from transcript_ui.launcher import OverlayProcess


def test_a_headless_session_skips_the_overlay_without_raising(monkeypatch):
    """Not being able to draw a transcript is not a reason to refuse to run
    the assistant - the common headless case should produce one clear log
    line, not a stack trace."""
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    overlay = OverlayProcess()

    assert overlay.start() is False
    assert overlay.is_running() is False


def test_a_failing_launch_is_swallowed(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    overlay = OverlayProcess(python_executable="/nonexistent/python")

    assert overlay.start() is False


def test_start_and_stop_manage_a_real_child_process(monkeypatch, tmp_path):
    """Driven with a stub interpreter rather than the real overlay, so this
    test needs no display and no GTK."""
    monkeypatch.setenv("DISPLAY", ":0")
    stub = tmp_path / "sleeper.py"
    stub.write_text("import time\nwhile True: time.sleep(0.05)\n")

    overlay = OverlayProcess(python_executable=sys.executable)
    # Point it at the stub instead of `-m transcript_ui`.
    overlay._python = sys.executable
    original_popen = subprocess.Popen

    def fake_popen(command, **kwargs):
        return original_popen([sys.executable, str(stub)], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    assert overlay.start() is True
    assert overlay.is_running() is True

    overlay.stop()

    assert overlay.is_running() is False


def test_start_is_idempotent_while_running(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPLAY", ":0")
    stub = tmp_path / "sleeper.py"
    stub.write_text("import time\nwhile True: time.sleep(0.05)\n")
    overlay = OverlayProcess()
    original_popen = subprocess.Popen
    calls = []

    def fake_popen(command, **kwargs):
        calls.append(command)
        return original_popen([sys.executable, str(stub)], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    try:
        overlay.start()
        overlay.start()
        assert len(calls) == 1
    finally:
        overlay.stop()


def test_stop_escalates_to_a_kill_when_sigterm_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPLAY", ":0")
    stubborn = tmp_path / "stubborn.py"
    stubborn.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True: time.sleep(0.05)\n"
    )
    overlay = OverlayProcess()
    original_popen = subprocess.Popen
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **kwargs: original_popen([sys.executable, str(stubborn)], **kwargs),
    )
    # Keep the test quick - the real timeout is 3s.
    monkeypatch.setattr("transcript_ui.launcher._TERMINATE_TIMEOUT_SECONDS", 0.3)

    assert overlay.start() is True
    time.sleep(0.2)
    overlay.stop()

    assert overlay.is_running() is False


def test_stop_is_safe_without_start():
    OverlayProcess().stop()


def test_stop_is_safe_twice(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPLAY", ":0")
    stub = tmp_path / "sleeper.py"
    stub.write_text("import time\nwhile True: time.sleep(0.05)\n")
    overlay = OverlayProcess()
    original_popen = subprocess.Popen
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **kwargs: original_popen([sys.executable, str(stub)], **kwargs),
    )
    overlay.start()
    overlay.stop()
    overlay.stop()
