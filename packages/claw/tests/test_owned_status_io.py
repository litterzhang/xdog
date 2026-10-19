"""Native readonly history/status IO must not own the event loop or its socket reader."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.claw.core.types import UserInput

from packages.claw.tests.test_gateway_inputs import gateway as gateway
from packages.claw.tests.test_gateway_inputs import receive, send


@pytest.mark.parametrize("hold", [False, True])
async def test_native_status_reads_off_loop_and_reader_can_serve_control_while_read_is_held(gateway, monkeypatch, hold):
    server, _entered, _release, contexts, endpoint = gateway
    app = server._orchestrator
    await app.route_message(UserInput(group_id="main", content="actual saved history"))
    runtime = app._runtimes["main"]
    session = runtime._active_session
    original = runtime.transcript_store.load_transcript
    owner = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    threads = []

    def actual_read(identifier):
        value = original(identifier)
        threads.append(threading.get_ident())
        entered.set()
        if hold and threading.get_ident() != owner:
            assert release.wait(5)
        return value

    reader, writer = await asyncio.open_unix_connection(endpoint)
    try:
        monkeypatch.setattr(runtime.transcript_store, "load_transcript", actual_read)
        await send(writer, {"type": "ping", "group_id": "main"})
        assert await asyncio.to_thread(entered.wait, 3)
        assert all(thread != owner for thread in threads), "Actual native status still reads history on the Agent loop"
        if hold:
            await send(writer, {"type": "status"})
            assert (await receive(reader, "status"))["running"] is True
            assert runtime._active_session is session and contexts == [["actual saved history"]]
        release.set()
        pong = await receive(reader, "pong")
        assert pong["session_id"] == session.meta.session_id and pong["history_count"] == 2
        await receive(reader, "history_end")
        assert runtime._active_session is session and contexts == [["actual saved history"]]
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()


async def test_status_read_failure_is_unavailable_not_fake_empty_history(gateway, monkeypatch):
    server, _entered, _release, contexts, endpoint = gateway
    app = server._orchestrator
    await app.route_message(UserInput(group_id="main", content="actual retained history"))
    runtime = app._runtimes["main"]
    session = runtime._active_session
    original = runtime.transcript_store.load_transcript
    path = runtime.transcript_store._path(session.meta.session_id)
    before = path.read_bytes()

    def actual_read_then_fail(identifier):
        original(identifier)
        raise OSError("actual status IO unavailable")

    reader, writer = await asyncio.open_unix_connection(endpoint)
    try:
        with monkeypatch.context() as fault:
            fault.setattr(runtime.transcript_store, "load_transcript", actual_read_then_fail)
            await send(writer, {"type": "ping", "group_id": "main"})
            error = await receive(reader, "error")
            assert "Status unavailable" in error["message"] and "history_count" not in error
        await send(writer, {"type": "ping", "group_id": "main"})
        assert (await receive(reader, "pong"))["history_count"] == 2
        await receive(reader, "history_end")
        assert path.read_bytes() == before and contexts == [["actual retained history"]]
        assert runtime._active_session is session
    finally:
        writer.close()
        await writer.wait_closed()


async def test_disconnect_and_repeated_shutdown_cancel_join_actual_status_reader_before_lease_release(
    gateway, monkeypatch,
):
    from xdog.claw.core.inputs.store import InputStore

    server, _entered, _release, contexts, endpoint = gateway
    app = server._orchestrator
    await app.route_message(UserInput(group_id="main", content="actual reader ownership"))
    runtime = app._runtimes["main"]
    original = runtime.transcript_store.load_transcript
    entered, release, exited = (threading.Event() for _ in range(3))

    def actual_read(identifier):
        value = original(identifier)
        entered.set()
        try:
            assert release.wait(5)
            return value
        finally:
            exited.set()

    reader, writer = await asyncio.open_unix_connection(endpoint)
    stopping = None
    try:
        monkeypatch.setattr(runtime.transcript_store, "load_transcript", actual_read)
        await send(writer, {"type": "ping", "group_id": "main"})
        assert await asyncio.to_thread(entered.wait, 3)
        writer.close()
        await writer.wait_closed()
        stopping = asyncio.create_task(server.stop())
        async with asyncio.timeout(3):
            while server._stop_task is None:
                await asyncio.sleep(0)
        for _ in range(3):
            stopping.cancel()
            await asyncio.sleep(0)
        assert not stopping.done() and not exited.is_set()
        with pytest.raises(BlockingIOError):
            await InputStore.open(app._inputs.store.path)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        assert exited.is_set() and contexts == [["actual reader ownership"]]
        reopened = await InputStore.open(app._inputs.store.path)
        await reopened.close()
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()
        if stopping is not None:
            await asyncio.gather(stopping, return_exceptions=True)
