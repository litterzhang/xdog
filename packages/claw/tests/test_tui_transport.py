"""Gateway I/O ownership, generation fencing, and UI recovery regressions."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock, Mock

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import strip_ansi


def connected_app() -> ChatApp:
    app = ChatApp("/tmp/unused-claw-test.sock")
    app._handle_response({"type": "pong", "session_id": "test-session", "history_count": 0})
    return app


def submit(app: ChatApp, text: str) -> None:
    app._editor.set_text(text)
    assert app._editor.handle_input(KeyEvent(key="enter"))


def plain(app: ChatApp) -> str:
    return "\n".join(strip_ansi(line) for line in app._chat_log.render(100))


async def test_write_failure_is_a_disconnect_and_cancels_blocked_reader() -> None:
    app = connected_app()
    submit(app, "active")
    writer = AsyncMock()
    writer.write = Mock()
    writer.write.side_effect = BrokenPipeError("write failed")
    reader = asyncio.StreamReader()
    disconnected = await asyncio.wait_for(app._run_connection(reader, writer), timeout=1)
    assert disconnected
    app._poll()
    assert app._active_run_id is None
    assert app._editor.get_text() == "active"
    assert app._state["connection_status"] != "connected"


def test_io_reconnect_notification_does_not_mutate_ui_state_before_poll() -> None:
    app = connected_app()
    before = dict(app._state)
    app._set_reconnect_status(2)
    assert app._state == before
    app._poll()
    assert app._state["connection_status"] == "reconnecting"


def test_long_outage_backoff_is_capped_without_huge_integer_conversion() -> None:
    assert connected_app()._reconnect_delay(100_000) == 30.0


@pytest.mark.parametrize("failure", [False, True])
def test_run_stops_io_worker_after_terminal_eof_or_error(monkeypatch, failure: bool) -> None:
    app = connected_app()
    started = threading.Event()

    def worker():
        started.set()
        while not app._exit_requested:
            time.sleep(0.005)

    def terminal():
        assert started.wait(1)
        if failure:
            raise RuntimeError("terminal failed")

    monkeypatch.setattr(app, "_io_loop", worker)
    app._io_thread = threading.Thread(target=app._io_loop, daemon=True)
    monkeypatch.setattr(app._tui, "start", terminal)
    try:
        if failure:
            with pytest.raises(RuntimeError, match="terminal failed"):
                app.run()
        else:
            app.run()
        assert app._exit_requested
        assert not app._io_thread.is_alive()
    finally:
        app._request_exit()
        app._io_thread.join(timeout=1)


async def test_cancelled_connection_owner_awaits_both_children() -> None:
    app = connected_app()
    reader = asyncio.StreamReader()
    writer = AsyncMock()
    writer.write = Mock()
    before = set(asyncio.all_tasks())
    owner = asyncio.create_task(app._run_connection(reader, writer))
    await asyncio.sleep(0.02)
    children = set(asyncio.all_tasks()) - before - {owner}
    assert children
    owner.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert all(task.done() for task in children)
    finally:
        for task in children:
            task.cancel()
        await asyncio.gather(*children, return_exceptions=True)


def test_offline_submission_preserves_text_without_scheduling_a_run() -> None:
    app = ChatApp("/tmp/unused-claw-test.sock")
    submit(app, "keep this draft")
    assert app._editor.get_text() == "keep this draft"
    assert app._active_run_id is None
    assert app._send_queue.empty()
    assert not app._waiting


def test_reconnect_socket_is_not_ready_until_session_handshake() -> None:
    app = connected_app()
    app._handle_response({"type": "internal_reconnected"})
    submit(app, "not yet")
    assert app._editor.get_text() == "not yet"
    assert app._send_queue.empty()
    assert app._state["connection_status"] != "connected"


def test_status_reply_does_not_stop_active_turn_or_reset_elapsed(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr("xdog.claw.channels.tui.tui_app.time.monotonic", lambda: clock[0])
    app = connected_app()
    submit(app, "active")
    app._set_activity_status("running")
    submit(app, "/status")
    clock[0] += 5
    app._handle_response({"type": "status", "groups": ["main"], "pid": 123})
    assert app._waiting
    assert app._state["activity_status"] == "running"
    assert app._format_elapsed() == "5s"


@pytest.mark.parametrize("command", ["/reset", "/clear"])
def test_destructive_local_commands_are_deferred_while_busy(command: str) -> None:
    app = connected_app()
    submit(app, "active")
    run_id = app._active_run_id
    before = plain(app)
    submit(app, command)
    assert app._active_run_id == run_id
    assert "active" in plain(app)
    requests = []
    while not app._send_queue.empty():
        requests.append(app._send_queue.get_nowait()["type"])
    assert requests == ["message"]
    assert before in plain(app)


def test_unscoped_gateway_rejection_restores_active_draft() -> None:
    app = connected_app()
    submit(app, "active")
    submit(app, "queued")
    app._editor.set_text("new draft")
    app._handle_response({"type": "error", "message": "Unknown group"})
    assert app._active_run_id is None
    assert not app._waiting
    assert app._editor.get_text() == "active\n\nqueued\n\nnew draft"
    assert "Unknown group" in plain(app)


def test_unscoped_command_error_does_not_cancel_active_run() -> None:
    app = connected_app()
    submit(app, "active")
    run_id = app._active_run_id
    submit(app, "/status")
    app._handle_response({"type": "error", "message": "Status unavailable"})
    assert "Status unavailable" in plain(app)
    assert app._active_run_id == run_id
    assert app._waiting


def test_abort_not_active_ack_releases_cancellation_latch() -> None:
    app = connected_app()
    submit(app, "active")
    run_id = app._active_run_id
    app._handle_escape()
    app._handle_response({"type": "abort_ack", "run_id": run_id, "status": "not_active"})
    assert app._active_run_id is None
    assert app._aborting_run_id is None
    assert app._editor.get_text() == "active"


async def test_invalid_frames_do_not_drop_the_connection_or_valid_following_event() -> None:
    app = connected_app()
    reader = asyncio.StreamReader()
    writer = AsyncMock()
    writer.write = Mock()
    reader.feed_data(b'[]\nnull\n{"broken":\n\xff\n')
    reader.feed_data(b'{"type":"pong"}\n{"type":"delta","run_id":"r","content":"valid"}\n')
    reader.feed_eof()
    assert await asyncio.wait_for(app._run_connection(reader, writer), 1)
    messages = []
    while not app._recv_queue.empty():
        messages.append(app._recv_queue.get_nowait())
    assert all(isinstance(message, dict) or hasattr(message, "items") for message in messages)
    assert any(message.get("content") == "valid" for message in messages)


async def test_ping_precedes_requests_and_stale_generation_is_never_sent() -> None:
    app = connected_app()
    app._send_queue.put({"type": "message", "content": "stale", "_connection_generation": -1})
    app._send_queue.put({"type": "status", "_connection_generation": 0})
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"type":"pong","history_count":0}\n')
    written = []
    sent_status = asyncio.Event()
    writer = AsyncMock()
    writer.write = Mock()

    def write(data):
        request = json.loads(data)
        written.append(request)
        if request["type"] == "status":
            sent_status.set()

    writer.write.side_effect = write
    owner = asyncio.create_task(app._run_connection(reader, writer))
    try:
        await asyncio.wait_for(sent_status.wait(), 1)
        assert [request["type"] for request in written] == ["ping", "status"]
        assert all("_connection_generation" not in request for request in written)
    finally:
        owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)


def test_history_sync_gate_keeps_draft_until_history_end() -> None:
    app = ChatApp("/tmp/unused-claw-test.sock")
    app._handle_response({"type": "_connection_opened", "generation": 1})
    app._handle_response({"type": "pong", "history_count": 1, "_connection_generation": 1})
    assert app._state["connection_status"] == "syncing"
    submit(app, "during sync")
    assert app._send_queue.empty() and app._active_run_id is None
    app._handle_response({"type": "history_end", "_connection_generation": 1})
    assert app._connection_ready
    assert app._editor.get_text() == "during sync"


def test_history_end_alone_cannot_make_an_unhandshaken_connection_ready() -> None:
    app = ChatApp("/tmp/unused-claw-test.sock")
    app._handle_response({"type": "history_end"})
    assert not app._connection_ready
    assert app._state["connection_status"] != "connected"


def test_old_connection_events_cannot_settle_new_active_work() -> None:
    app = connected_app()
    app._handle_response({"type": "_connection_opened", "generation": 1})
    app._handle_response({"type": "pong", "_connection_generation": 1})
    submit(app, "new run")
    run_id = app._active_run_id
    app._handle_response({"type": "internal_error", "message": "old error", "_connection_generation": 0})
    app._handle_response({"type": "status", "groups": [], "_connection_generation": 0})
    assert app._active_run_id == run_id
    assert app._waiting and app._connection_ready
    assert "old error" not in plain(app)


def test_user_can_queue_while_status_request_is_in_flight() -> None:
    app = connected_app()
    submit(app, "active")
    submit(app, "/status")
    submit(app, "queued")
    assert tuple(app._pending_messages) == ("queued",)
    assert app._editor.get_text() == ""


def test_pending_reset_blocks_new_run_and_error_releases_wait_state() -> None:
    app = connected_app()
    submit(app, "/reset")
    submit(app, "next message")
    assert app._active_run_id is None
    assert app._editor.get_text() == "next message"
    app._handle_response({"type": "error", "message": "Reset unavailable"})
    assert app._pending_command is None
    assert not app._waiting
    assert "Reset unavailable" in plain(app)


def test_exit_interrupts_real_io_worker_during_long_backoff(tmp_path, monkeypatch) -> None:
    app = ChatApp(str(tmp_path / "missing.sock"))
    app._RECONNECT_BASE_DELAY = app._RECONNECT_MAX_DELAY = 30.0
    backoff = threading.Event()
    sleep = asyncio.sleep

    async def wait(delay):
        backoff.set()
        await sleep(delay)

    monkeypatch.setattr("xdog.claw.channels.tui.tui_app.asyncio.sleep", wait)
    app._io_thread.start()
    try:
        assert backoff.wait(1)
        app._request_exit()
        app._io_thread.join(timeout=1)
        assert not app._io_thread.is_alive()
        assert app._io_task is None and app._io_loop_ref is None
    finally:
        app._request_exit()
        app._io_thread.join(timeout=1)


def test_exit_interrupts_blocked_drain_and_closes_owned_writer(monkeypatch) -> None:
    app = connected_app()
    draining = threading.Event()
    writer = AsyncMock()
    writer.write = Mock()
    writer.close = Mock()
    async def connect(*args, **kwargs):
        return asyncio.StreamReader(), writer  # Construct on the owning loop.

    async def drain():
        draining.set()
        await asyncio.Event().wait()

    writer.drain.side_effect = drain
    monkeypatch.setattr(
        "xdog.claw.channels.tui.tui_app.asyncio.open_unix_connection", AsyncMock(side_effect=connect),
    )
    app._io_thread.start()
    try:
        assert draining.wait(1)
        assert app._io_thread.is_alive() and app._io_task is not None
        app._request_exit()
        app._io_thread.join(timeout=1)
        assert not app._io_thread.is_alive()
        writer.close.assert_called_once_with()
        writer.wait_closed.assert_awaited_once_with()
    finally:
        app._request_exit()
        app._io_thread.join(timeout=1)


async def test_real_socket_reconnect_restores_once_and_never_resends_uncertain_input(tmp_path) -> None:
    socket_path = str(tmp_path / "gateway.sock")
    app = ChatApp(socket_path)
    app._RECONNECT_BASE_DELAY = 0.01
    received = []
    connections = [0]
    server_tasks = set()

    async def serve(reader, writer):
        task = asyncio.current_task()
        server_tasks.add(task)
        connections[0] += 1
        number = connections[0]
        try:
            first = json.loads(await reader.readline())
            received.append((number, first))
            writer.write(b'{"type":"pong","session_id":"test-session","history_count":0}\n')
            await writer.drain()
            while line := await reader.readline():
                request = json.loads(line)
                received.append((number, request))
                if number == 1:
                    return  # Lose the active message's acknowledgement/outcome.
                if request["type"] == "status":
                    event = {"type": "status", "groups": ["main"]}
                else:
                    event = {"type": "response", "run_id": request["run_id"], "content": "recovered answer"}
                writer.write((json.dumps(event) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            server_tasks.discard(task)

    async def poll_until(predicate):
        async with asyncio.timeout(3):
            while True:
                app._poll()
                if predicate():
                    return
                await asyncio.sleep(0.005)

    server = await asyncio.start_unix_server(serve, path=socket_path)
    owner = asyncio.create_task(app._io_loop_async())
    try:
        await poll_until(lambda: app._connection_ready)
        submit(app, "active")
        submit(app, "queued")
        app._editor.set_text("draft")
        await poll_until(lambda: app._connection_generation == 2 and app._connection_ready)
        restored = "active\n\nqueued\n\ndraft"
        assert app._editor.get_text() == restored
        assert app._active_run_id is None and not app._pending_messages
        app._send_queue.put({
            "type": "message", "content": "stale request", "run_id": "old", "_connection_generation": 1,
        })
        app._queue_request({"type": "status"})
        await poll_until(lambda: any(number == 2 and request["type"] == "status" for number, request in received))
        assert app._editor.get_text() == restored
        assert [request["type"] for number, request in received if number == 2] == ["ping", "status"]
        assert all("_connection_generation" not in request for _, request in received)
        app._editor.handle_input(KeyEvent(key="enter"))  # Explicit resubmission only.
        await poll_until(lambda: app._active_run_id is None and "recovered answer" in plain(app))
        sent = [request["content"] for _, request in received if request["type"] == "message"]
        assert sent == ["active", restored]
    finally:
        app._request_exit()
        owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)
        server.close()
        await server.wait_closed()
        if server_tasks:
            await asyncio.wait_for(asyncio.gather(*server_tasks), timeout=1)


@pytest.mark.parametrize("reply", [
    {"type": "status", "groups": ["main"]},
    {"type": "error", "message": "Status unavailable"},
])
def test_queue_waits_for_pending_status_then_resumes_without_losing_messages(reply) -> None:
    app = connected_app()
    submit(app, "active")
    run_id = app._active_run_id
    submit(app, "/status")
    submit(app, "queued one")
    submit(app, "queued two")
    app._handle_response({"type": "final", "run_id": run_id})
    assert app._active_run_id is None
    assert tuple(app._pending_messages) == ("queued one", "queued two")
    app._handle_response(reply)
    assert app._active_text == "queued one"
    assert tuple(app._pending_messages) == ("queued two",)


def test_owned_terminal_log_notification_does_not_disturb_active_run(monkeypatch) -> None:
    import logging
    app = connected_app()
    submit(app, "active")
    run_id = app._active_run_id
    app._io_thread = threading.Thread(target=app._io_stop.wait, daemon=True)

    def terminal():
        logging.getLogger("xdog.claw").warning("gateway diagnostic")
        app._poll()
        assert "gateway diagnostic" in plain(app)
        assert app._active_run_id == run_id and app._waiting

    monkeypatch.setattr(app._tui, "start", terminal)
    app.run()
    assert not app._io_thread.is_alive()


def test_scoped_validation_rejection_with_status_pending_restores_run_not_command() -> None:
    app = connected_app()
    submit(app, "rejected run")
    run_id = app._active_run_id
    submit(app, "/status")
    app._handle_response({"type": "error", "run_id": run_id, "message": "Unknown group: missing"})
    assert app._active_run_id is None
    assert app._editor.get_text() == "rejected run"
    app._handle_response({"type": "status", "groups": ["main"]})
    assert app._pending_command is None and not app._waiting
