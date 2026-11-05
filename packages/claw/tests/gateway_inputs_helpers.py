"""Real Unix-socket controls route into the one SDK Agent, not a second tool loop."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.application.gateway import GatewayServer
from xdog.claw.config import ClawConfig, GroupDef

from packages.claw.tests.core_inputs import model_input_bodies


@pytest.fixture
async def gateway(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    contexts = []

    def stream_fn(model, context, options=None):
        users = [message.content if isinstance(message.content, str) else "".join(part.text for part in message.content)
                 for message in model_input_bodies(context)]
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
    with patch("xdog.claw.application.gateway._build_model_and_options", return_value=("test/dummy", stream_fn)):
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
