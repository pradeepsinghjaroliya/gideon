"""Consistent logging setup shared by every module's scripts/tests and the
orchestrator, so log output looks the same everywhere.
"""

from __future__ import annotations

import logging

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
