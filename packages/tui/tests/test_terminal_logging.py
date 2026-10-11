from __future__ import annotations

import io
import logging
import threading

import pytest
from xdog.tui.event_queue import EventQueue
from xdog.tui.terminal_logging import capture_terminal_logs


@pytest.mark.parametrize("failure", [False, True])
def test_capture_routes_console_logs_preserves_files_and_restores_handlers(tmp_path, monkeypatch, failure) -> None:
    root = logging.getLogger()
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    console = logging.StreamHandler(stream)
    logfile = tmp_path / "logs.txt"
    file_handler = logging.FileHandler(logfile)
    root.addHandler(console)
    root.addHandler(file_handler)
    before = list(root.handlers)
    events = EventQueue()
    try:
        with pytest.raises(RuntimeError) if failure else _no_error():
            with capture_terminal_logs(events):
                logging.getLogger("xdog.test").warning("token=abc123 hello\x1b[2J")
                if failure:
                    raise RuntimeError("terminal failed")
        assert root.handlers == before
        assert stream.getvalue() == ""
        message = events.get_nowait()
        assert message["type"] == "log_message"
        assert message["message"] == "token=<redacted> hello"
        assert "abc123" in logfile.read_text()  # Existing file log policy is unchanged.
    finally:
        root.removeHandler(console)
        root.removeHandler(file_handler)
        file_handler.close()


def _no_error():
    from contextlib import nullcontext
    return nullcontext()


def test_last_resort_stderr_is_not_used_when_no_root_handler_exists(monkeypatch) -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    for handler in before:
        root.removeHandler(handler)
    events = EventQueue()
    try:
        with capture_terminal_logs(events):
            logging.getLogger("xdog.test").error("error without handler")
        assert stream.getvalue() == ""
        assert events.get_nowait()["message"] == "error without handler"
        assert not root.handlers
    finally:
        for handler in before:
            root.addHandler(handler)


def test_background_logger_publishes_owned_message_not_a_mutable_record() -> None:
    events = EventQueue()
    args = {"message": "original"}
    with capture_terminal_logs(events):
        producer = threading.Thread(target=lambda: logging.getLogger("xdog.test").warning("%(message)s", args))
        producer.start()
        producer.join(timeout=1)
        args["message"] = "changed"
    assert events.get_nowait()["message"] == "original"


def test_malformed_log_format_does_not_print_handler_traceback() -> None:
    events = EventQueue()
    from xdog.tui.terminal_logging import _UILogHandler
    handler = _UILogHandler(events)
    handler.handle(logging.LogRecord("xdog.test", logging.WARNING, __file__, 0, "%d", ("not an integer",), None))
    assert events.get_nowait()["message"] == "Unable to format log message"
    handler.close()


def test_console_event_size_is_bounded() -> None:
    events = EventQueue()
    with capture_terminal_logs(events):
        logging.getLogger("xdog.test").warning("x" * 10000)
    message = events.get_nowait()["message"]
    assert len(message) < 4200
    assert "5904 log characters omitted" in message
