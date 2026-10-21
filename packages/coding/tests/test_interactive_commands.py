"""Slash commands share session ownership without blocking terminal input."""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from xdog.ai.types import TextContent, UserMessage
from xdog.coding.core.slash_commands import CommandResult
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import strip_ansi


def app(working_dir: str = "/workspace") -> InteractiveMode:
    session = MagicMock()
    session.runtime = None
    session.children.runtime.aclose = AsyncMock()
    session.agent.subscribe.return_value = lambda: None
    session.agent.options.thinking = "medium"
    session.permissions.mode = "ask"
    session.model = "test-model"
    session.session_id = "test-session"
    session.messages = []
    session.working_dir = working_dir
    session.context_limit = 1000
    ui = InteractiveMode(session)
    # Presentation tests use a mock session; real owner lifecycle is exercised
    # by test_native_session_queue and the real-session EOF regression below.
    ui._session_events.start = lambda: None
    return ui


def plain(component) -> str:
    return "\n".join(strip_ansi(row) for row in component.render(100))


def finish_command(ui) -> None:
    assert ui._command_thread is not None
    ui._command_thread.join(timeout=1)
    assert not ui._command_thread.is_alive()
    ui._poll()


@pytest.mark.parametrize("command,args", [
    ("clear", ""), ("compact", ""), ("branch", "saved"), ("fork", ""),
    ("model", "new-model"), ("thinking", "high"), ("unload", "all"), ("skill-name", "request"),
])
def test_mutating_commands_do_not_race_an_active_agent(monkeypatch, command, args) -> None:
    ui = app()
    ui._set_busy(True)
    ui._worker_active = True
    ui._chat_log.add_user("retained chat")
    execute = AsyncMock()
    monkeypatch.setattr("xdog.coding.modes.interactive.interactive_mode.execute_command", execute)
    ui._handle_slash_command(command, args)
    assert not execute.called
    assert "retained chat" in plain(ui._chat_log)
    assert ui._worker_active
    assert f"/{command}" in ui._editor.get_text()


def test_command_exception_is_a_ui_event_not_thread_traceback(monkeypatch) -> None:
    ui = app()
    failures = []
    monkeypatch.setattr(threading, "excepthook", failures.append)
    monkeypatch.setattr(
        "xdog.coding.modes.interactive.interactive_mode.execute_command",
        AsyncMock(side_effect=ValueError("invalid model")),
    )
    ui._handle_slash_command("model", "bad")
    finish_command(ui)
    assert not failures
    assert "invalid model" in plain(ui._chat_log)
    assert not ui._is_busy
    assert ui._command is None


def test_read_only_command_preserves_active_phase_and_timer(monkeypatch) -> None:
    ui = app()
    ui._set_busy(True)
    started = ui._busy_started
    monkeypatch.setattr(
        "xdog.coding.modes.interactive.interactive_mode.execute_command",
        AsyncMock(return_value=CommandResult("session metadata")),
    )
    ui._handle_slash_command("session", "")
    finish_command(ui)
    assert ui._is_busy and ui._busy_started == started
    assert "session metadata" in plain(ui._chat_log)


def test_mutating_command_owns_busy_state_and_preserves_new_submissions(monkeypatch) -> None:
    ui = app()
    entered = threading.Event()
    release = threading.Event()

    async def execute(*args):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.005)
        return CommandResult("compacted")

    monkeypatch.setattr("xdog.coding.modes.interactive.interactive_mode.execute_command", execute)
    try:
        ui._handle_slash_command("compact", "")
        assert entered.wait(1)
        assert ui._is_busy and "compact" in plain(ui._footer)
        ui._start_turn("preserved draft")
        assert ui._editor.get_text() == "preserved draft"
        assert not ui._worker_active
        release.set()
        finish_command(ui)
        assert not ui._is_busy
        assert ui._editor.get_text() == "preserved draft"
    finally:
        release.set()
        ui._request_exit()


def test_only_one_session_command_runs_at_a_time(monkeypatch) -> None:
    ui = app()
    entered = threading.Event()
    calls = []

    async def execute(command, args, session):
        calls.append(command)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("xdog.coding.modes.interactive.interactive_mode.execute_command", execute)
    try:
        ui._handle_slash_command("compact", "")
        assert entered.wait(1)
        ui._handle_slash_command("model", "next")
        assert calls == ["compact"]
        assert "/model next" in ui._editor.get_text()
        ui._handle_escape()
        finish_command(ui)
    finally:
        ui._request_exit()


def test_escape_cancels_command_and_never_cancels_unrelated_agent(monkeypatch) -> None:
    ui = app()
    entered = threading.Event()
    stopped = threading.Event()

    async def execute(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr("xdog.coding.modes.interactive.interactive_mode.execute_command", execute)
    try:
        ui._handle_slash_command("compact", "")
        assert entered.wait(1)
        ui._editor.set_text("draft")
        ui._tui._dispatch_input(KeyEvent(key="escape"))
        finish_command(ui)
        assert stopped.wait(1)
        assert ui._editor.get_text() == "draft"
        assert "Cancelled /compact" in plain(ui._chat_log)
        assert not ui._is_busy
        ui._session.cancel.assert_not_called()
    finally:
        ui._request_exit()


def test_exit_cancels_owned_command_worker(monkeypatch) -> None:
    ui = app()
    entered = threading.Event()
    stopped = threading.Event()

    async def execute(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr("xdog.coding.modes.interactive.interactive_mode.execute_command", execute)

    def terminal():
        ui._handle_slash_command("compact", "")
        assert entered.wait(1)

    monkeypatch.setattr(ui._tui, "start", terminal)
    ui.run()
    assert stopped.wait(1)
    assert not ui._command_thread.is_alive()
    assert ui._command_loop_ref is None and ui._command_task is None


def test_successful_clear_updates_transcript_but_preserves_input_history(monkeypatch) -> None:
    ui = app()
    ui._chat_log.add_user("OLD CHAT")
    ui._editor.add_to_history("old input")
    monkeypatch.setattr(
        "xdog.coding.modes.interactive.interactive_mode.execute_command",
        AsyncMock(return_value=CommandResult("Conversation cleared.")),
    )
    ui._handle_slash_command("clear", "")
    finish_command(ui)
    assert "OLD CHAT" not in plain(ui._chat_log)
    assert "Conversation cleared." in plain(ui._chat_log)
    ui._editor.handle_input(KeyEvent(key="up"))
    assert ui._editor.get_text() == "old input"


def test_changed_branch_replays_current_context_and_ignores_old_turn_events(monkeypatch) -> None:
    ui = app()
    old_stamp = ui._active_stamp
    ui._chat_log.add_user("OLD BRANCH")

    async def execute(*args):
        ui._session.messages = [UserMessage(content=(TextContent(text="NEW BRANCH"),))]
        return CommandResult("Restored branch: saved")

    monkeypatch.setattr("xdog.coding.modes.interactive.interactive_mode.execute_command", execute)
    ui._handle_slash_command("branch", "saved")
    finish_command(ui)
    ui._handle_ui_event({"type": "text_update", "text": "STALE", "stamp": old_stamp})
    transcript = plain(ui._chat_log)
    assert "NEW BRANCH" in transcript
    assert "OLD BRANCH" not in transcript
    assert "STALE" not in transcript


def test_skill_followup_does_not_execute_after_cancellation_race(monkeypatch) -> None:
    ui = app()
    monkeypatch.setattr(
        "xdog.coding.modes.interactive.interactive_mode.execute_command",
        AsyncMock(return_value=CommandResult("Activated skill", prompt="follow-up")),
    )
    ui._handle_slash_command("skill", "follow-up")
    ui._command_thread.join(timeout=1)  # Result is queued, not yet shown.
    ui._handle_escape()
    ui._poll()
    assert not ui._worker_active
    assert "follow-up" in ui._editor.get_text()


def test_owned_terminal_routes_background_warnings_to_chat_not_stderr(monkeypatch) -> None:
    import io
    import logging
    import sys
    ui = app()
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    console = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(console)

    def terminal():
        logging.getLogger("xdog.agent").warning("visible warning")
        ui._poll()
        assert "visible warning" in plain(ui._chat_log)
        assert stream.getvalue() == ""

    monkeypatch.setattr(ui._tui, "start", terminal)
    try:
        ui.run()
        assert console in root.handlers
    finally:
        root.removeHandler(console)


def test_stale_command_result_cannot_settle_a_successor(monkeypatch) -> None:
    ui = app()
    entered = threading.Event()

    async def execute(command, args, session):
        if command == "model":
            return CommandResult("model metadata")
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("xdog.coding.modes.interactive.interactive_mode.execute_command", execute)
    ui._handle_slash_command("model", "")
    old_id = ui._command.id
    finish_command(ui)
    try:
        ui._handle_slash_command("compact", "")
        assert entered.wait(1)
        current = ui._command
        ui._handle_ui_event({"type": "command_result", "command_id": old_id, "output": "STALE", "prompt": "do not run"})
        assert ui._command is current
        assert ui._is_busy and not ui._worker_active
        assert "STALE" not in plain(ui._chat_log)
        ui._handle_escape()
        finish_command(ui)
    finally:
        ui._request_exit()


def test_live_coding_file_completion_is_polled_and_closed(tmp_path) -> None:
    (tmp_path / "review.py").touch()
    ui = app(str(tmp_path))
    ui._editor.set_text("Read @rev")
    deadline = time.monotonic() + 3
    try:
        while time.monotonic() < deadline and ui._editor._select_list is None:
            time.sleep(0.005)
            ui._poll()
        assert ui._editor._select_list is not None
        assert ui._editor._select_list.selected_value == "@review.py"
        ui._editor.handle_input(KeyEvent(key="enter"))
        assert ui._editor.get_text() == "Read @review.py"
        assert not ui._is_busy
        assert not ui._worker_active
    finally:
        ui._request_exit()
    assert ui._file_completions._closed
