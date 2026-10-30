from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, ImageContent, Model, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.claw.tests.core_inputs import model_input_bodies, transcript_bodies

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=")


@pytest.fixture
async def gateway(tmp_path):
    contexts = []

    def stream_fn(model, context, options):
        contexts.append(context)
        message = AssistantMessage(content=(TextContent(text="answer"),))
        async def generate():
            yield DoneEvent(message=message)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)

    config = ClawConfig(model="test/vision", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "gateway.sock"), pid_file=str(tmp_path / "gateway.pid"),
                        groups=(GroupDef(id="main", is_main=True),))
    info = Model(id="test/vision", input=("text", "image"))
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/vision", stream_fn)), \
         patch("xdog.claw.core.runtime.group.GroupRuntime.image_model_info", return_value=info):
        await server.start()
        try:
            yield server, config.socket_path, contexts
        finally:
            await server.stop()


async def send(writer, data):
    writer.write((json.dumps(data) + "\n").encode())
    await writer.drain()


async def read(reader, kind):
    async def until():
        while True:
            value = json.loads(await reader.readline())
            if value["type"] == kind:
                return value
    return await asyncio.wait_for(until(), 3)


async def upload(reader, writer, key):
    for index, data in enumerate((PNG[:20], PNG[20:])):
        await send(writer, dict(type="attachment_chunk", id=key, index=index, mime_type="image/png",
                               data=base64.b64encode(data).decode(), final=index == 1,
                               sha256=hashlib.sha256(PNG).hexdigest()))
        assert "error" not in await read(reader, "attachment_ack")


async def test_connection_scoped_image_upload_reaches_sdk_and_persists_typed_parts(gateway):
    server, path, contexts = gateway
    reader, writer = await asyncio.open_unix_connection(path)
    other_reader, other_writer = await asyncio.open_unix_connection(path)
    try:
        await send(writer, dict(type="ping"))
        pong = await read(reader, "pong")
        assert pong["attachment_transport"] == 1 and pong["image_input"] is True
        await upload(reader, writer, "image")
        await send(other_writer, dict(type="message", run_id="foreign", group_id="main", content="inspect",
                                      image_refs=["image"]))
        rejected = await read(other_reader, "error")
        assert rejected["run_id"] == "foreign"
        assert not contexts
        await send(writer, dict(type="message", run_id="owned", group_id="main", content="", image_refs=["image"]))
        await read(reader, "response")
        user = model_input_bodies(contexts[0])[0]
        assert isinstance(user.content[1], ImageContent)
        assert base64.b64decode(user.content[1].data) == PNG
        runtime = server._orchestrator._runtimes["main"]
        meta = runtime.transcript_store.get_active_session("main")
        transcript = runtime.transcript_store.load_transcript(meta.session_id)
        assert transcript[0]["type"] == "xdog.claw.actor.v1"
        transcript = transcript_bodies(transcript)
        assert transcript[0]["content"][1]["type"] == "image"
        assert runtime.get_session_info()["history"][0]["image_count"] == 1
        assert "data" not in runtime.get_session_info()["history"][0]["content"][-1]
    finally:
        writer.close()
        other_writer.close()
        await writer.wait_closed()
        await other_writer.wait_closed()


async def test_incomplete_image_reference_is_rejected_before_agent_or_tools(gateway):
    _server, path, contexts = gateway
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        await send(writer, dict(type="attachment_chunk", id="partial", index=0, mime_type="image/png",
                               data=base64.b64encode(PNG[:20]).decode(), final=False))
        await read(reader, "attachment_ack")
        await send(writer, dict(
            type="message", run_id="bad", group_id="main", content="inspect", image_refs=["partial"],
        ))
        assert (await read(reader, "error"))["run_id"] == "bad"
        assert not contexts
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize("refs", [42, "image", {"id": "image"}, [None], ["image", None]])
async def test_malformed_image_refs_settle_without_closing_connection(gateway, refs):
    _server, path, contexts = gateway
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        await upload(reader, writer, "image")
        await send(writer, dict(type="message", run_id="malformed", content="inspect", image_refs=refs))
        assert (await read(reader, "error"))["run_id"] == "malformed"
        assert not contexts
        await send(writer, dict(type="ping"))
        assert (await read(reader, "pong"))["attachment_transport"] == 1
        if isinstance(refs, list) and "image" in refs:
            # Recognizable refs in a malformed list are released too.
            await upload(reader, writer, "image")
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize("payload", [
    dict(type="message", run_id="bad", group_id="missing", content="inspect"),
    dict(type="message", run_id="bad", session_id="stale", content="inspect"),
    dict(type="message", run_id="bad", content=42),
    dict(type="input_control", run_id="missing", request_id="control", input_id="one",
         action="add", delivery="steer", content="inspect"),
])
async def test_terminal_rejections_release_upload_cache(gateway, payload):
    _server, path, contexts = gateway
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        # More than the cache's identity limit. Every rejection must release
        # both identity and byte accounting on the still-live connection.
        for index in range(20):
            key = f"image-{index}"
            await upload(reader, writer, key)
            await send(writer, {**payload, "image_refs": [key], "request_id": f"control-{index}"})
            result = await read(reader, "input_ack" if payload["type"] == "input_control" else "error")
            assert result.get("success") is not True
        assert not contexts
        await upload(reader, writer, "image-0")
    finally:
        writer.close()
        await writer.wait_closed()


async def test_large_image_chunks_bypass_single_frame_limit_and_retain_exact_bytes(gateway):
    _server, path, contexts = gateway
    reader, writer = await asyncio.open_unix_connection(path)
    raw = PNG + b"\0" * (1200 * 1024)
    checksum = hashlib.sha256(raw).hexdigest()
    try:
        for index, offset in enumerate(range(0, len(raw), 64 * 1024)):
            block = raw[offset:offset + 64 * 1024]
            await send(writer, dict(type="attachment_chunk", id="large", index=index, mime_type="image/png",
                                   data=base64.b64encode(block).decode(), final=offset + len(block) == len(raw),
                                   sha256=checksum))
            assert "error" not in await read(reader, "attachment_ack")
        await send(writer, dict(type="message", run_id="large", content="", image_refs=["large"]))
        await read(reader, "response")
        user = model_input_bodies(contexts[0])[0]
        assert base64.b64decode(user.content[1].data) == raw
    finally:
        writer.close()
        await writer.wait_closed()


async def test_image_validation_yield_rechecks_session_lease_before_start(gateway):
    server, path, contexts = gateway
    reader, writer = await asyncio.open_unix_connection(path)
    other_reader, other_writer = await asyncio.open_unix_connection(path)
    entered, release = threading.Event(), threading.Event()
    original = server._orchestrator.validate_images

    def validate(group, images):
        original(group, images)
        entered.set()
        assert release.wait(3)

    try:
        await send(writer, dict(type="ping"))
        lease = (await read(reader, "pong")).get("session_id", "")
        await upload(reader, writer, "image")
        with patch.object(server._orchestrator, "validate_images", side_effect=validate):
            await send(writer, dict(type="message", run_id="lease", content="inspect", session_id=lease,
                                   image_refs=["image"]))
            assert await asyncio.to_thread(entered.wait, 3)
            await send(other_writer, dict(type="session_control", request_id="new", action="new",
                                         expected_session=lease, group_id="main"))
            assert (await read(other_reader, "session_result"))["success"]
            await read(other_reader, "session_changed")
            release.set()
            rejected = await read(reader, "error")
            assert rejected["run_id"] == "lease" and "session changed" in rejected["message"].lower()
        assert not contexts and not server._orchestrator.is_group_running("main")
    finally:
        release.set()
        writer.close()
        other_writer.close()
        await writer.wait_closed()
        await other_writer.wait_closed()


@pytest.mark.parametrize("abort", [False, True])
async def test_image_follow_up_edit_consume_or_return_uses_identified_owner(gateway, abort):
    server, path, _contexts = gateway
    reader, writer = await asyncio.open_unix_connection(path)
    entered, release = asyncio.Event(), asyncio.Event()
    contexts = []
    runtime = server._orchestrator._runtimes["main"]

    def stream_fn(model, context, options):
        contexts.append(context)
        message = AssistantMessage(content=(TextContent(text="answer"),))
        first = len(contexts) == 1

        async def generate():
            yield StartEvent(partial=AssistantMessage())
            if first:
                entered.set()
                await release.wait()
            yield DoneEvent(message=message)

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)

    runtime.stream_fn = stream_fn
    try:
        await send(writer, dict(type="message", run_id="active", content="hold"))
        await read(reader, "run_ack")
        await asyncio.wait_for(entered.wait(), 3)
        await upload(reader, writer, "follow-image")
        await send(writer, dict(type="input_control", run_id="active", request_id="add", input_id="one",
                               action="add", delivery="follow_up", content="", image_refs=["follow-image"]))
        ack = await read(reader, "input_ack")
        assert ack["success"] and ack["snapshot"][0]["image_count"] == 1
        await send(writer, dict(type="input_control", run_id="active", request_id="edit", input_id="one",
                               action="edit", content="edited"))
        assert (await read(reader, "input_ack"))["success"]
        pending = runtime._active_session.agent.pending_inputs()[0]
        assert pending.content == "edited" and base64.b64decode(pending.images[0].data) == PNG
        if abort:
            await send(writer, dict(type="abort", run_id="active", group_id="main"))
            await read(reader, "abort_ack")
        release.set()
        receipt = await read(reader, "input_returned" if abort else "input_consumed")
        assert receipt["id"] == "one" and receipt["content"] == "edited"
        await read(reader, "aborted" if abort else "response")
        assert not runtime._active_session.agent.pending_inputs()
        users = [message for message in model_input_bodies(contexts[-1])]
        if abort:
            assert len(users) == 1
        else:
            assert users[-1].content[0].text == "edited"
            assert base64.b64decode(users[-1].content[1].data) == PNG
            meta = runtime.transcript_store.get_active_session("main")
            rows = runtime.transcript_store.load_transcript(meta.session_id)
            assert any(row["role"] == "user" and isinstance(row["content"], list)
                       and any(part.get("type") == "image" for part in row["content"]) for row in rows)
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()
