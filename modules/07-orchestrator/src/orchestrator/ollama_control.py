"""Start/stop/health-check for the local Ollama server, for the tray's
LLM dashboard control (see `main.py`).

Ollama's own installer sets up `ollama.service` as a **system** (not user)
systemd unit, which needs `sudo systemctl start/stop ollama` - per this
project's standing rule, the assistant must never run `sudo` itself, and
this machine's `ollama.service` is disabled/inactive anyway (confirmed via
`systemctl status ollama` - the user runs `ollama serve` manually instead).
So this module manages a plain `ollama serve` process directly, entirely
within the user's own privileges - no systemd/sudo involved.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Callable

# (url) -> True if reachable, matching `requests.get(url, timeout=...).ok`
HealthCheckFn = Callable[[str], bool]
# argv -> a Popen-like handle, just needs to exist (return value unused)
PopenFn = Callable[[list[str]], object]
# argv -> completed, matching `subprocess.run`
RunFn = Callable[[list[str]], object]
# name -> full path or None, matching `shutil.which`
WhichFn = Callable[[str], "str | None"]


class OllamaControlError(RuntimeError):
    """Raised when `ollama` can't be found or launched, with a clear
    message - a bare exception from here would otherwise vanish silently:
    pystray/GTK menu-item click callbacks that raise don't surface the
    error anywhere the user would see it, which was a real, confirmed
    symptom ("clicking it doesn't do ollama serve for me")."""


def _default_health_check(url: str) -> bool:
    import requests

    try:
        response = requests.get(url, timeout=1.0)
    except requests.exceptions.RequestException:
        return False
    return response.ok


class OllamaControl:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        health_check: HealthCheckFn | None = None,
        popen: PopenFn | None = None,
        run: RunFn | None = None,
        which: WhichFn | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._health_check = health_check or _default_health_check
        self._popen = popen or (lambda argv: subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        ))
        self._run = run or (lambda argv: subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        self._which = which or shutil.which

    def is_running(self) -> bool:
        return self._health_check(f"{self._base_url}/api/tags")

    def start(self) -> None:
        """No-op (besides launching the process) if it's already running -
        `ollama serve` itself would just fail to bind the port, which is
        harmless and not worth checking for first."""
        binary = self._which("ollama")
        if binary is None:
            raise OllamaControlError("'ollama' not found on PATH - is it installed?")
        try:
            self._popen([binary, "serve"])
        except OSError as exc:
            raise OllamaControlError(f"failed to start 'ollama serve': {exc}") from exc

    def stop(self) -> None:
        """Kills any `ollama serve` process owned by this user - not
        necessarily one `start()` launched itself, since the user may have
        started it manually before Gideon did. No sudo: `pkill` only ever
        matches the calling user's own processes here."""
        self._run(["pkill", "-f", "ollama serve"])
