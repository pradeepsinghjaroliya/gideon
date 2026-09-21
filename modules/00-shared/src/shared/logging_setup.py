"""Consistent logging setup shared by every module's scripts/tests and the
orchestrator, so log output looks the same everywhere.
"""

from __future__ import annotations

import logging
from typing import Callable

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(name: str, level: int = logging.INFO) -> logging.Logger:
    """Return a logger configured with the shared format.

    Safe to call multiple times for the same or different names - won't
    stack duplicate handlers.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        logger.addHandler(handler)
        logger.propagate = False

    return logger


class CallbackHandler(logging.Handler):
    """Forwards each record's plain "name: message" text to a callback,
    instead of a stream/file - lets a UI (e.g. `07-orchestrator/main.py`
    wiring the "agentic"/"agentic.tools" loggers into the tray dashboard's
    activity log, see `text_input.tray.TrayApp.append_log`) receive log
    lines from a logger it otherwise has no visibility into, without that
    logger's own module needing to know the UI exists. Add alongside
    `setup_logging()`'s own `StreamHandler` (`logger.addHandler(...)`) -
    it doesn't replace it, so terminal output is unaffected.

    A callback that raises is swallowed via `handleError` (matching every
    other `logging.Handler.emit`'s contract) so a UI hiccup can never take
    down logging for the rest of the app.
    """

    def __init__(self, callback: Callable[[str], None], level: int = logging.NOTSET) -> None:
        super().__init__(level)
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._callback(f"{record.name}: {record.getMessage()}")
        except Exception:
            self.handleError(record)
