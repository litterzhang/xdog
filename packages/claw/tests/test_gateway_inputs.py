"""Real Unix-socket controls route into the one SDK Agent, not a second tool loop."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer


@pytest.fixture
async def gateway(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    contexts = []

    def stream_fn(model, context, options=None):
        users = [message.content if isinstance(message.content, str) else "".join(part.text for part in message.content)
                 for message in context.messages if isinstance(message, UserMessage)]
        contexts.append(users)
        message = AssistantMessage(content=(TextContent(text="answer"),))

        async def generate():
            yield StartEvent(partial=AssistantMessage())
            if users[-1] == "HOLD":
                entered.set()
                await release.wait()
            yield DoneEvent(message=message)

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)

    config = ClawConfig(model="test/dummy", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "gateway.sock"), pid_file=str(tmp_path / "gateway.pid"),
                        groups=(GroupDef(id="main", name="Test", workspace=str(tmp_path / "workspace"), is_main=True),))
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/dummy", stream_fn)):
        await server.start()
        try:
            yield server, entered, release, contexts, config.socket_path
        finally:
            release.set()
            await asyncio.wait_for(server.stop(), 5)


async def send(writer, value):
    writer.write((json.dumps(value) + "\n").encode())
    await writer.drain()


async def receive(reader, kind, *, request_id=None):
    async def until():
        while True:
            line = await reader.readline()
            assert line, "gateway closed unexpectedly"
            value = json.loads(line)
            if value["type"] == kind and (request_id is None or value.get("request_id") == request_id):
                return value
    return await asyncio.wait_for(until(), 3)


def control(action, input_id="i1", *, request_id="c1", content="", delivery="", direction=0, run_id="active"):
    return dict(type="input_control", run_id=run_id, group_id="main", action=action, input_id=input_id,
                request_id=request_id, content=content, delivery=delivery, direction=direction)


async def start(reader, writer, entered):
    await send(writer, dict(type="ping"))
    assert (await receive(reader, "pong"))["input_controls"] == 1
    await send(writer, dict(type="message", run_id="active", group_id="main", content="HOLD"))
    await receive(reader, "run_ack")
    await asyncio.wait_for(entered.wait(), 3)


async def test_owned_add_edit_remove_move_consumption_and_persistence(gateway) -> None:
    server, entered, release, contexts, socket_path = gateway
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        await start(reader, writer, entered)
        for number, (content, delivery) in enumerate((("steer", "steer"), ("follow", "follow_up"),
                                                      ("remove", "follow_up"), ("last", "follow_up")), 1):
            await send(writer, control(
                "add", f"i{number}", request_id=f"c{number}", content=content, delivery=delivery,
            ))
            assert (await receive(reader, "input_ack", request_id=f"c{number}"))["success"]
        for request in (control("edit", "i2", request_id="edit", content="edited"),
                        control("move", "i4", request_id="move", direction=-1),
                        control("remove", "i3", request_id="remove")):
            await send(writer, request)
            ack = await receive(reader, "input_ack", request_id=request["request_id"])
            assert ack["success"]
        # Move again: last precedes edited inside the follow-up queue.
        await send(writer, control("move", "i4", request_id="move-again", direction=-1))
        assert (await receive(reader, "input_ack", request_id="move-again"))["success"]
        release.set()
        receipts = []
        while True:
            value = json.loads(await asyncio.wait_for(reader.readline(), 3))
            if value["type"] == "input_consumed":
                receipts.append((value["id"], value["content"]))
            if value["type"] in {"final", "response"}:
                break
        assert receipts == [("i1", "steer"), ("i4", "last"), ("i2", "edited")]
        assert contexts[-1] == ["HOLD", "steer", "last", "edited"]
        assert server._orchestrator is not None
        info = server._orchestrator.get_session_info("main")
        history_users = [entry for entry in info["history"] if entry["role"] == "user"]
        assert len(history_users) == 4
        runtime = server._orchestrator._runtimes["main"]
        assert not runtime._active_session.agent.pending_inputs()
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()


async def test_foreign_run_duplicate_and_malformed_requests_do_not_abort_owner(gateway) -> None:
    server, entered, release, _contexts, socket_path = gateway
    reader, writer = await asyncio.open_unix_connection(socket_path)
    other_reader, other_writer = await asyncio.open_unix_connection(socket_path)
    try:
        await start(reader, writer, entered)
        await send(other_writer, control("add", content="foreign", delivery="steer"))
        assert not (await receive(other_reader, "input_ack"))["success"]
        request = control("add", content="one", delivery="steer")
        await send(writer, request)
        first = await receive(reader, "input_ack")
        assert first["success"]
        await send(writer, request)
        assert await receive(reader, "input_ack") == first
        await send(writer, {**request, "content": "changed"})
        assert not (await receive(reader, "input_ack"))["success"]
        for index, change in enumerate(({"action": []}, {"delivery": {}}, {"content": "\ud800"},
                                       {"content": "x" * (64 * 1024 + 1)}, {"direction": True, "action": "move"})):
            malformed = {**request, **change, "request_id": f"bad-{index}"}
            await send(writer, malformed)
            assert not (await receive(reader, "input_ack", request_id=f"bad-{index}"))["success"]
        assert server._orchestrator is not None
        items = server._orchestrator._runtimes["main"]._active_session.agent.pending_inputs()
        assert len(items) == 1 and items[0].content == "one"
        release.set()
        await receive(reader, "input_consumed")
        await receive(reader, "response")
    finally:
        release.set()
        writer.close()
        other_writer.close()
        await writer.wait_closed()
        await other_writer.wait_closed()


async def test_abort_returns_unconsumed_inputs_and_does_not_leak_into_a_successor(gateway) -> None:
    server, entered, release, contexts, socket_path = gateway
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        await start(reader, writer, entered)
        await send(writer, control("add", content="return me", delivery="follow_up"))
        assert (await receive(reader, "input_ack"))["success"]
        await send(writer, dict(type="abort", group_id="main", run_id="active"))
        await receive(reader, "abort_ack")
        release.set()
        returned = await receive(reader, "input_returned")
        assert returned["id"] == "i1" and returned["content"] == "return me"
        await receive(reader, "aborted")
        assert server._orchestrator is not None
        assert not server._orchestrator._runtimes["main"]._active_session.agent.pending_inputs()
        # Allow the gateway's owned-run reservation release callback to settle.
        await asyncio.sleep(0.02)
        await send(writer, dict(type="message", run_id="next", group_id="main", content="NEXT"))
        await receive(reader, "response")
        assert "return me" not in contexts[-1]
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()


async def test_oversized_and_nonobject_frames_do_not_destroy_an_owned_run(gateway) -> None:
    _server, entered, release, _contexts, socket_path = gateway
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        await start(reader, writer, entered)
        # Include a successor control frame in the same write: rejected bytes
        # must not be reparsed or swallow the valid successor.
        writer.write(b"x" * (1024 * 1024 + 100) + b"\n" +
                     (json.dumps(control("add", content="valid", delivery="steer")) + "\n").encode())
        await writer.drain()
        await receive(reader, "protocol_error")
        assert (await receive(reader, "input_ack"))["success"]
        await send(writer, [])
        await receive(reader, "protocol_error")
        release.set()
        assert (await receive(reader, "input_consumed"))["content"] == "valid"
        await receive(reader, "response")
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()
