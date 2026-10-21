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


@pytest.mark.parametrize("failure", [False, True])
def test_represented_tool_errors_only_skip_ui_keep_tracebacks_and_restore(tmp_path, monkeypatch, failure) -> None:
    root = logging.getLogger()
    logger = logging.getLogger("xdog.agent.agent_loop")
    levels = (root.level, logger.level)
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    console = logging.StreamHandler(stream)
    logfile = tmp_path / "tool-errors.txt"
    file_handler = logging.FileHandler(logfile)
    file_handler.setFormatter(logging.Formatter("%(tool_call_id)s %(message)s"))
    root.addHandler(console)
    root.addHandler(file_handler)
    before = list(root.handlers)
    events = EventQueue()
    try:
        with pytest.raises(RuntimeError, match="terminal failed") if failure else _no_error():
            with capture_terminal_logs(events):
                try:
                    raise ValueError("original tool failure")
                except ValueError:
                    logger.exception(
                        "Tool %s execution failed", "agent",
                        extra={"xdog_tool_error_represented": True, "tool_call_id": "call-42"},
                    )
                assert events.empty()
                assert (root.level, logger.level) == levels
                if failure:
                    raise RuntimeError("terminal failed")
        assert root.handlers == before
        assert (root.level, logger.level) == levels
        assert stream.getvalue() == ""
        text = logfile.read_text()
        assert "call-42 Tool agent execution failed" in text
        assert "Traceback (most recent call last)" in text
        assert "ValueError: original tool failure" in text
        # Capture is gone: the same represented record reaches the old console.
        logger.error(
            "after restore", extra={"xdog_tool_error_represented": True, "tool_call_id": "call-43"},
        )
        assert "after restore" in stream.getvalue()
        assert events.empty()
    finally:
        root.removeHandler(console)
        root.removeHandler(file_handler)
        file_handler.close()


@pytest.mark.parametrize(("name", "level", "extra"), [
    ("xdog.agent.agent_loop", logging.ERROR, {}),
    ("xdog.agent.agent_loop", logging.ERROR, {"tool_call_id": "call-1"}),
    ("xdog.agent.agent_loop", logging.ERROR, {"xdog_tool_error_represented": True}),
    ("xdog.agent.agent_loop", logging.ERROR, {"xdog_tool_error_represented": True, "tool_call_id": ""}),
    ("xdog.agent.agent_loop", logging.ERROR, {"xdog_tool_error_represented": True, "tool_call_id": 1}),
    ("xdog.agent.agent_loop", logging.ERROR, {"xdog_tool_error_represented": "true", "tool_call_id": "call-1"}),
    ("xdog.agent.agent_loop", logging.ERROR, {"xdog_tool_error_represented": False, "tool_call_id": "call-1"}),
    ("xdog.agent.agent_loop.child", logging.ERROR, {"xdog_tool_error_represented": True, "tool_call_id": "call-1"}),
    ("xdog.tools.agent", logging.ERROR, {"xdog_tool_error_represented": True, "tool_call_id": "call-1"}),
    ("xdog.agent.agent_loop", logging.WARNING, {"xdog_tool_error_represented": True, "tool_call_id": "call-1"}),
    ("xdog.agent.agent_loop", logging.CRITICAL, {"xdog_tool_error_represented": True, "tool_call_id": "call-1"}),
])
def test_unrepresented_errors_and_warnings_still_reach_ui(name, level, extra) -> None:
    events = EventQueue()
    with capture_terminal_logs(events):
        logging.getLogger(name).log(level, "Tool agent execution failed", extra=extra)
    event = events.get_nowait()
    assert event == {
        "type": "log_message", "logger": name,
        "level": logging.getLevelName(level), "message": "Tool agent execution failed",
    }
    assert events.empty()


def test_suppressed_tool_error_does_not_fall_back_to_stderr(monkeypatch) -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    for handler in before:
        root.removeHandler(handler)
    events = EventQueue()
    try:
        with capture_terminal_logs(events):
            logging.getLogger("xdog.agent.agent_loop").error(
                "Tool agent execution failed",
                extra={"xdog_tool_error_represented": True, "tool_call_id": "call-1"},
            )
        assert events.empty()
        assert stream.getvalue() == ""
        assert not root.handlers
    finally:
        for handler in before:
            root.addHandler(handler)


@pytest.mark.parametrize("execution", ["sequential", "parallel"])
async def test_actual_agent_tool_failure_has_one_outcome_and_no_duplicate_log(execution, caplog) -> None:
    agent = pytest.importorskip("xdog.agent")
    from xdog.agent.agent_loop import _execute_tool_calls
    from xdog.ai.types import AssistantMessage, ToolCall

    async def execute(*args, **kwargs):
        # A genuine warning from the tool must survive alongside its failure.
        logging.getLogger("xdog.tools.agent").warning("follow-up warning")
        raise RuntimeError("follow-up unavailable")

    tool = agent.AgentTool(name="agent", execute=execute)
    assistant = AssistantMessage(content=(ToolCall(id="follow-up-1", name="agent", arguments={}),))
    events = EventQueue()

    tool_events = []

    async def emit(event):
        tool_events.append(event)

    with capture_terminal_logs(events):
        results = await _execute_tool_calls(
            agent.AgentContext(system_prompt="", messages=[], tools=[tool]), assistant,
            agent.AgentLoopConfig(tool_execution=execution), None, emit,
        )
    published = []
    while not events.empty():
        published.append(events.get_nowait())
    logs = [event for event in published if event["type"] == "log_message"]
    assert len(logs) == 1
    assert logs[0]["message"] == "follow-up warning"
    ends = [event for event in tool_events if isinstance(event, agent.ToolExecutionEndEvent)]
    assert len(ends) == 1
    assert ends[0].is_error and results[0].is_error
    assert ends[0].tool_call_id == results[0].tool_call_id == "follow-up-1"
    assert ends[0].result.content == results[0].content
    # An independent root sink still receives the exception, not just the UI.
    records = [record for record in caplog.records if record.name == "xdog.agent.agent_loop"]
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert isinstance(records[0].exc_info[1], RuntimeError)
