"""Starting and stopping the overlay as a child process.

`07-orchestrator`'s `main.py` uses this when `transcript_ui.autostart` is
on, so the user runs one command and gets both the assistant and its
on-screen transcript. It lives in this module rather than in the
orchestrator because "how the overlay is launched" is this module's
business - the orchestrator only needs `start()`/`stop()`.

The overlay deliberately cannot be a thread in the orchestrator process:
`TrayApp.run()` already owns the main thread with Tkinter's event loop, and
GTK needs a main loop of its own on the main thread. A child process also
means a crash in the UI costs the transcript and nothing else - the
conversation keeps working.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

# Long enough for the GTK loop to notice SIGTERM, unlink its socket and
# exit; short enough that quitting the assistant still feels immediate.
_TERMINATE_TIMEOUT_SECONDS = 3.0


class OverlayProcess:
    def __init__(
        self,
        socket_path: Path | str | None = None,
        logger: logging.Logger | None = None,
        python_executable: str | None = None,
    ) -> None:
        self._socket_path = str(socket_path) if socket_path else None
        self._log = logger or logging.getLogger("transcript_ui.launcher")
        self._python = python_executable or sys.executable
        self._process: subprocess.Popen | None = None

    def start(self) -> bool:
        """Launch the overlay. Returns whether it started.

        A failure here is logged and swallowed: not being able to draw a
        transcript is not a reason to refuse to run the assistant. The most
        likely cause by far is a headless session (no `$DISPLAY` and no
        `$WAYLAND_DISPLAY`), which is checked for up front so the common
        case produces one clear log line instead of a stack trace.
        """
        if self._process is not None and self._process.poll() is None:
            return True

        if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
            self._log.info(
                "no graphical display detected - skipping the transcript overlay "
                "(the assistant itself is unaffected)"
            )
            return False

        command = [self._python, "-m", "transcript_ui"]
        if self._socket_path:
            command += ["--socket", self._socket_path]
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                # Inherit stdout/stderr so the overlay's own log lines land
                # in the same terminal (or journald unit) as everything
                # else - a separate pipe nobody reads would eventually fill
                # and block the child.
                start_new_session=False,
            )
        except OSError:
            self._log.warning("could not start the transcript overlay", exc_info=True)
            return False
        self._log.info("started the transcript overlay (pid %d)", self._process.pid)
        return True

    def stop(self) -> None:
        """Terminate the overlay, escalating to SIGKILL if it will not go.

        SIGTERM first so it can unlink its socket on the way out - skipping
        straight to SIGKILL would leave a stale socket file behind for the
        next run to clean up (which `TranscriptServer._prepare_socket_path`
        handles, but there is no reason to create the mess).
        """
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        self._log.info("stopping the transcript overlay")
        try:
            process.terminate()
            process.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self._log.warning("transcript overlay did not exit, killing it")
            process.kill()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            self._log.debug("error stopping the transcript overlay", exc_info=True)

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None
