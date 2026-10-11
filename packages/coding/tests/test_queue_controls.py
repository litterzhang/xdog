from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import strip_ansi


def coding() -> InteractiveMode:
    session = MagicMock()
    session.agent.options.thinking = "off"
    session.agent.options.stream_options = None
    session.agent.state.is_streaming = False
    session.context_limit = 100_000
    session.working_dir = "/tmp"
    session._maybe_compact = AsyncMock()
    session.initialize_extensions = AsyncMock()
    session.extensions.event = AsyncMock()
    session.extensions.commands.return_value = {}
    return InteractiveMode(session)


def plain(component) -> str:
    return "\n".join(strip_ansi(row) for row in component.render(120))


@pytest.mark.parametrize("kind", ["coding", "claw"])
def test_local_queue_ids_survive_edit_move_remove_and_preserve_live_draft(kind) -> None:
    ui = coding() if kind == "coding" else ChatApp("/tmp/unused-queue.sock")
    try:
        if kind == "coding":
            ui._worker_active = True
            ui._pending_messages.extend((("one", True), ("two", True), ("three", True)))
            def texts():
                return [text for text, _echo in ui._pending_messages]
        else:
            ui._pending_messages.extend(("one", "two", "three"))
            def texts():
                return list(ui._pending_messages)
        ui._editor.set_text("live draft")
        ui._queue_command("")
        assert "q1" in plain(ui._chat_log)
        ui._queue_command("edit q2 changed")
        ui._queue_command("move q2 up")
        assert texts() == ["changed", "one", "three"]
        assert list(ui._pending_ids.ids) == ["q2", "q1", "q3"]
        ui._queue_command("remove q1")
        assert texts() == ["changed", "three"]
        ui._queue_command("remove q1")
        assert "no longer pending" in plain(ui._chat_log)
        assert ui._editor.get_text() == "live draft"
    finally:
        ui._request_exit()


def test_claw_edited_slash_message_is_literal_not_a_gateway_command() -> None:
    ui = ChatApp("/tmp/unused-queue.sock")
    ui._handle_response({"type": "pong"})
    ui._pending_messages.append("old")
    ui._queue_command("edit q1 /reset")
    ui._dispatch_next_pending()
    request = ui._send_queue.get_nowait()
    assert request["type"] == "message" and request["content"] == "/reset"
    ui._request_exit()


def test_coding_live_owner_controls_and_consumption_receipts() -> None:
    entered, release = threading.Event(), threading.Event()
    contexts = []

    def stream_fn(model, context, options=None):
        contexts.append([message.content if isinstance(message.content, str)
                         else "".join(part.text for part in message.content)
                         for message in context.messages if isinstance(message, UserMessage)])
        message = AssistantMessage(content=(TextContent(text="answer"),))

        async def generate():
            yield StartEvent(partial=AssistantMessage())
            if len(contexts) == 1:
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(0.005)
            yield DoneEvent(message=message)

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)

    ui = coding()
    agent = Agent(stream_fn, config=AgentConfig(model="test/dummy"))
    ui._session.agent = agent
    ui._session.cancel = lambda **kwargs: agent.abort()

    def wait_for(predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            ui._poll()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError(plain(ui._chat_log))

    try:
        ui._start_turn("original")
        assert entered.wait(3)
        ui._send_runtime_input("steer", "steer one")
        ui._send_runtime_input("follow_up", "follow one")
        ui._send_runtime_input("follow_up", "remove me")
        wait_for(lambda: len(ui._routed_inputs) == 3 and all(item.state == "accepted"
                                                          for item in ui._routed_inputs.values()))
        ui._queue_command("edit i2 edited follow")
        ui._queue_command("remove i3")
        wait_for(lambda: "i3" not in ui._routed_inputs and ui._routed_inputs["i2"].content == "edited follow")
        ui._editor.set_text("live draft")
        release.set()
        wait_for(lambda: not ui._worker_active)
        ui._poll()
        assert contexts[-1] == ["original", "steer one", "edited follow"]
        assert not ui._routed_inputs
        assert ui._editor.get_text() == "live draft"
        assert plain(ui._chat_log).count("steer one") >= 1
        assert not agent.pending_inputs()
    finally:
        release.set()
        ui._request_exit()
        if ui._worker_thread:
            ui._worker_thread.join(3)


def test_coding_idle_explicit_input_preserves_text_without_polluting_next_turn() -> None:
    ui = coding()
    ui._editor.set_text("newer draft")
    ui._send_runtime_input("steer", "unsent")
    assert ui._editor.get_text() == "unsent\n\nnewer draft"
    assert not ui._routed_inputs
    assert not ui._worker_active
    ui._request_exit()


@pytest.mark.parametrize("kind", ["coding", "claw"])
def test_local_queue_overflow_preserves_submission_instead_of_dropping_old_messages(kind) -> None:
    ui = coding() if kind == "coding" else ChatApp("/tmp/unused-queue.sock")
    try:
        if kind == "coding":
            ui._worker_active = True
            ui._pending_messages.extend((str(i), True) for i in range(50))
            ui._start_turn("overflow")
        else:
            ui._handle_response({"type": "pong"})
            ui._active_run_id = "active"
            ui._pending_messages.extend(str(i) for i in range(50))
            ui._handle_submit("overflow")
        assert len(ui._pending_messages) == 50
        assert ui._editor.get_text() == "overflow"
    finally:
        ui._request_exit()


def test_claw_ack_consumed_returned_and_unknown_receipts_are_distinct() -> None:
    ui = ChatApp("/tmp/unused-queue.sock")
    ui._handle_response({"type": "pong", "input_controls": 1})
    ui._handle_submit("original")
    ui._send_queue.get_nowait()
    ui._send_runtime_input("steer", "steer text")
    request = ui._send_queue.get_nowait()
    ui._handle_response({"type": "input_ack", "request_id": request["request_id"], "run_id": ui._active_run_id,
                         "success": True, "snapshot": [{"id": "i1", "delivery": "steer"}]})
    assert ui._routed_inputs["i1"].state == "accepted"
    assert "queued 1" in plain(ui._footer)
    ui._handle_response({"type": "input_consumed", "id": "i1", "content": "steer text", "run_id": "wrong"})
    assert "i1" in ui._routed_inputs
    ui._handle_response({"type": "input_consumed", "id": "i1", "content": "steer text", "run_id": ui._active_run_id})
    assert not ui._routed_inputs
    ui._send_runtime_input("follow_up", "follow text")
    ui._editor.set_text("live draft")
    ui._handle_response({"type": "input_returned", "id": "i2", "content": "follow text", "run_id": ui._active_run_id})
    assert ui._editor.get_text() == "follow text\n\nlive draft"
    ui._handle_response({"type": "input_returned", "id": "i2", "content": "follow text", "run_id": ui._active_run_id})
    assert ui._editor.get_text().count("follow text") == 1
    ui._request_exit()


def test_claw_disconnect_after_final_preserves_orphaned_uncertain_control_without_replay() -> None:
    ui = ChatApp("/tmp/unused-queue.sock")
    ui._handle_response({"type": "pong", "input_controls": 1})
    ui._handle_submit("original")
    ui._send_runtime_input("steer", "uncertain")
    ui._handle_response({"type": "final", "run_id": ui._active_run_id})
    ui._handle_response({"type": "internal_error", "message": "lost ack"})
    assert "uncertain" in ui._editor.get_text()
    assert not ui._routed_inputs
    ui._handle_response({"type": "_connection_opened", "generation": 1})
    ui._handle_response({"type": "pong", "input_controls": 1})
    assert ui._active_run_id is None
    ui._request_exit()


def test_claw_legacy_gateway_never_receives_an_unknown_control_request() -> None:
    ui = ChatApp("/tmp/unused-queue.sock")
    ui._handle_response({"type": "pong"})
    ui._handle_submit("original")
    ui._send_queue.get_nowait()
    ui._send_runtime_input("steer", "preserve")
    assert ui._send_queue.empty()
    assert ui._editor.get_text() == "preserve"
    assert ui._active_run_id is not None
    ui._request_exit()


def test_claw_cancellation_defers_runtime_restore_until_authoritative_receipt() -> None:
    ui = ChatApp("/tmp/unused-queue.sock")
    ui._handle_response({"type": "pong", "input_controls": 1})
    ui._handle_submit("original")
    ui._send_runtime_input("steer", "already consumed")
    ui._send_runtime_input("follow_up", "not consumed")
    run_id = ui._active_run_id
    ui._handle_escape()
    assert "already consumed" not in ui._editor.get_text()
    assert "not consumed" not in ui._editor.get_text()
    ui._handle_response({"type": "input_consumed", "run_id": run_id, "id": "i1", "content": "already consumed"})
    ui._handle_response({"type": "input_returned", "run_id": run_id, "id": "i2", "content": "not consumed"})
    assert "already consumed" not in ui._editor.get_text()
    assert ui._editor.get_text().count("not consumed") == 1
    ui._handle_response({"type": "aborted", "run_id": run_id})
    assert not ui._routed_inputs
    ui._request_exit()


def test_runtime_shortcuts_cannot_confirm_or_bypass_coding_permission() -> None:
    from xdog.coding.core.permissions import PermissionRequest

    ui = coding()
    ui._show_permission_request(PermissionRequest(id="approval", tool_name="bash", arguments={}, summary="command"))
    for event in (KeyEvent("s", alt=True), KeyEvent("f", alt=True), KeyEvent("q", ctrl=True)):
        ui._tui._dispatch_input(event)
    ui._session.permissions.resolve.assert_not_called()
    assert not ui._routed_inputs
    ui._request_exit()


def test_malformed_claw_receipt_keeps_input_uncertain_then_restores_it_without_replay() -> None:
    ui = ChatApp("/tmp/unused-queue.sock")
    ui._handle_response({"type": "pong", "input_controls": 1})
    ui._handle_submit("original")
    ui._send_queue.get_nowait()
    ui._send_runtime_input("steer", "keep me")
    request = ui._send_queue.get_nowait()
    run_id = ui._active_run_id
    for malformed in ({"success": "false", "snapshot": []}, {"success": True, "snapshot": [{}]}):
        ui._handle_response({"type": "input_ack", "run_id": run_id, "request_id": request["request_id"], **malformed})
        assert ui._routed_inputs["i1"].state == "sending"
        assert request["request_id"] in ui._input_requests
    ui._handle_response({"type": "input_consumed", "run_id": run_id, "id": "i1", "content": {}})
    assert "i1" in ui._routed_inputs
    ui._editor.set_text("live draft")
    ui._handle_response({"type": "final", "run_id": run_id})
    assert ui._editor.get_text() == "keep me\n\nlive draft"
    assert not ui._routed_inputs
    assert not ui._input_requests
    assert ui._send_queue.empty()
    ui._request_exit()


def test_coding_cancel_restores_only_unconsumed_runtime_inputs_once() -> None:
    entered = threading.Event()

    def stream_fn(model, context, options=None):
        async def generate():
            entered.set()
            yield StartEvent(partial=AssistantMessage())
            while not options.cancel.is_set():
                await asyncio.sleep(0.005)
            yield DoneEvent(message=AssistantMessage(stop_reason="aborted"))

        future = asyncio.get_running_loop().create_future()
        future.set_result(AssistantMessage(stop_reason="aborted"))
        return EventStream.from_async_generator(generate(), result_future=future)

    ui = coding()
    agent = Agent(stream_fn, config=AgentConfig(model="test/dummy"))
    ui._session.agent = agent
    ui._session.cancel = lambda **kwargs: agent.abort()
    try:
        ui._start_turn("original")
        assert entered.wait(3)
        ui._send_runtime_input("steer", "return steer")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and ui._routed_inputs["i1"].state != "accepted":
            ui._poll()
            time.sleep(0.005)
        assert ui._routed_inputs["i1"].state == "accepted"
        ui._editor.set_text("live draft")
        ui._cancel_active_work()
        while time.monotonic() < deadline and ui._worker_active:
            ui._poll()
            time.sleep(0.005)
        ui._poll()
        assert not ui._worker_active
        assert ui._editor.get_text() == "return steer\n\nlive draft"
        assert not ui._routed_inputs
        assert not agent.pending_inputs()
        ui._poll()
        assert ui._editor.get_text().count("return steer") == 1
    finally:
        ui._request_exit()
        if ui._worker_thread:
            ui._worker_thread.join(3)
