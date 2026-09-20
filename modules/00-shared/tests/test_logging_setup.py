import logging
import re

from shared.logging_setup import CallbackHandler, setup_logging

# setup_logging sets propagate=False on purpose (so a module's own handler
# doesn't double-log via any root handler the caller might configure), so
# we assert on the actual emitted text via capsys rather than caplog (which
# captures via the root logger and would miss non-propagating records).
_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[INFO\] test\.logger: hello world$"
)


def test_log_line_format(capsys):
    logger = setup_logging("test.logger")
    logger.info("hello world")

    captured = capsys.readouterr()
    assert _LINE_RE.match(captured.err.strip())


def test_setup_logging_does_not_duplicate_handlers():
    logger_a = setup_logging("test.no_dup")
    logger_b = setup_logging("test.no_dup")

    assert logger_a is logger_b
    assert len(logger_a.handlers) == 1


def test_callback_handler_forwards_name_and_message():
    received = []
    logger = logging.getLogger("test.callback_handler")
    logger.setLevel(logging.INFO)
    logger.addHandler(CallbackHandler(received.append))

    logger.info("hello %s", "world")

    assert received == ["test.callback_handler: hello world"]


def test_callback_handler_swallows_a_raising_callback():
    def broken_callback(message: str) -> None:
        raise RuntimeError("UI is gone")

    logger = logging.getLogger("test.callback_handler_broken")
    logger.setLevel(logging.INFO)
    handler = CallbackHandler(broken_callback)
    handler.handleError = lambda record: None  # silence pytest's "logging error" capture
    logger.addHandler(handler)

    logger.info("must not raise")  # must not propagate the callback's exception
