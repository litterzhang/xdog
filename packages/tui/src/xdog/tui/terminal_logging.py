"""Route root-console warnings to immutable UI events while a terminal is owned.

File handlers and other non-console sinks continue to receive their records.
The logging configuration is restored on every exit. Applications decide how
log events appear; producers never render or write directly to the terminal.
"""
from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from xdog.tui.event_queue import EventQueue
from xdog.tui.utils import redact_sensitive_text


class _UILogHandler(logging.Handler):
    def __init__(self, events: EventQueue) -> None:
        super().__init__(level=logging.WARNING)
        self._events = events

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            message = "Unable to format log message"
        # Do not send mutable LogRecord objects or formatted tracebacks across
        # threads. The complete record still reaches existing file handlers.
        message = redact_sensitive_text(message)
        if len(message) > 4096:
            message = message[:4096] + f"\n<{len(message) - 4096} log characters omitted>"
        self._events.put({
            "type": "log_message", "logger": record.name,
            "level": record.levelname, "message": message,
        })


def _is_terminal_handler(handler: logging.Handler) -> bool:
    if not isinstance(handler, logging.StreamHandler) or isinstance(handler, logging.FileHandler):
        return False
    stream = handler.stream
    return any(stream is target for target in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__)) or bool(
        getattr(stream, "isatty", lambda: False)()
    )


@contextmanager
def capture_terminal_logs(events: EventQueue) -> Iterator[None]:
    """Temporarily replace root-console sinks; also prevent stderr lastResort.

    This captures standard logging, not arbitrary stdout/stderr writes by an
    extension or a handler attached directly to a non-propagating child logger.
    """
    root = logging.getLogger()
    previous = list(root.handlers)
    capture = _UILogHandler(events)
    for handler in previous:
        if _is_terminal_handler(handler):
            root.removeHandler(handler)
    root.addHandler(capture)
    try:
        yield
    finally:
        root.removeHandler(capture)
        # Preserve handlers installed during the session, restoring original
        # identities/order without duplicating file/non-console handlers.
        added = [handler for handler in root.handlers if handler not in previous]
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in previous + added:
            root.addHandler(handler)
        capture.close()
