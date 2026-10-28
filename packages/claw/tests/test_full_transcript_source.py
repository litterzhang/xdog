from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
from dataclasses import asdict
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.source_reader import SourceReceiver
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.runtime.transcript_source import SourceCapture, prepare_source
from xdog.tui.transcript_browser import TranscriptRecord


@pytest.fixture
async def gateway(tmp_path):
    calls = []
    def stream_fn(model, context, options):
        calls.append(context)
        message = AssistantMessage(content=(TextContent(text="answer"),))
        async def generate():
            yield DoneEvent(message=message)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)
    config = ClawConfig(model="test/source", data_dir=str(tmp_path / "data"), socket_path=str(tmp_path / "source.sock"),
                        pid_file=str(tmp_path / "source.pid"),
                        groups=(GroupDef(id="main", is_main=True), GroupDef(id="other")))
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/source", stream_fn)):
        await server.start()
        try:
            yield server, config.socket_path, calls
        finally:
            await server.stop()


async def send(writer, data):
    writer.write((json.dumps(data) + "\n").encode())
    await writer.drain()


async def read(reader, kind):
    async def until():
        while True:
            raw = await reader.readline()
            assert raw
            message = json.loads(raw)
            if message["type"] == kind:
                return message
    return await asyncio.wait_for(until(), 5)


async def source(reader, writer, session, *, nonce="source", group="main"):
    await send(writer, dict(type="source_read", request_id=nonce, group_id=group, session_id=session))
    received = []
    receiver = SourceReceiver(received.append)
    receiver.expect(nonce, group, session)
    while True:
        raw = await asyncio.wait_for(reader.readline(), 5)
        assert raw and len(raw) < 100_000
        message = json.loads(raw)
        if message.get("request_id") != nonce:
            continue
        assert message["type"].startswith("source_")
        receiver.feed(message)
        if received and received[-1]["type"] in {"source_ready", "source_failed"}:
            break
    receiver.close()
    assert received[-1]["type"] == "source_ready", received[-1]
    return received[-1]["records"]


async def test_foreign_session_unknown_group_and_context_rotation_fence_source(gateway):
    server, path, calls = gateway
    runtime = server._orchestrator._runtimes["main"]
    meta = runtime.transcript_store.create_session("main")
    runtime.transcript_store.replace_transcript(meta.session_id, [{"role": "user", "content": "one"},
                                                                {"role": "assistant", "content": "two"}])
    other = server._orchestrator._runtimes["other"].transcript_store.create_session("other")
    reader, writer = await asyncio.open_unix_connection(path, limit=1_048_576)
    peer_reader, peer_writer = await asyncio.open_unix_connection(path, limit=1_048_576)
    entered, release = threading.Event(), threading.Event()
    from xdog.claw.core.runtime import source_protocol
    original = source_protocol.prepare_source
    def prepare(capture, cancel):
        result = original(capture, cancel)
        entered.set()
        assert release.wait(5)
        return result
    try:
        for group, session in (("main", other.session_id), ("main", "../foreign"), ("unknown", meta.session_id)):
            await send(writer, dict(type="source_read", request_id="bad", group_id=group, session_id=session))
            assert (await read(reader, "source_error"))["request_id"] == "bad"
        with patch.object(source_protocol, "prepare_source", side_effect=prepare):
            await send(writer, dict(type="source_read", request_id="held", group_id="main", session_id=meta.session_id))
            assert await asyncio.to_thread(entered.wait, 5)
            # Context rotation invalidates an already prepared source read;
            # historical goto/route switching no longer exists in Claw.
            await send(peer_writer, dict(type="session_control", request_id="new", action="new", group_id="main",
                                         expected_session=meta.session_id))
            assert (await read(peer_reader, "session_result"))["success"]
            await read(peer_reader, "session_changed")
            release.set()
            assert "changed" in (await read(reader, "source_error"))["error"].lower()
        current = runtime.get_new_session_id()
        assert current != meta.session_id
        assert len(await source(reader, writer, current, nonce="fresh")) == 0
        assert len(runtime.transcript_store.load_transcript(meta.session_id)) == 2
        # Source requests remain model-free. Rotation alone maintains memory and summarizes.
        assert len(calls) == 2
        assert [tool.name for tool in calls[0].tools] == ['memory']
        assert isinstance(calls[1].system_prompt, str)
        assert calls[1].system_prompt.startswith('You are a conversation summarizer.')
        assert not calls[1].tools
    finally:
        release.set()
        writer.close()
        peer_writer.close()
        await writer.wait_closed()
        await peer_writer.wait_closed()


def test_receiver_order_checksum_failure_stale_owner_and_no_partial_publish():
    record = TranscriptRecord("m", "assistant", "text", "body")
    data = (json.dumps(asdict(record)) + "\n").encode()
    published = []
    receiver = SourceReceiver(published.append)
    owner = dict(request_id="read", group_id="main", session_id="sid", revision=0)
    receiver.expect("read", "main", "sid")
    receiver.feed({"type": "source_begin", **owner, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    receiver.feed({"type": "source_chunk", **owner, "index": 1, "data": base64.b64encode(data).decode()})
    assert published[-1]["type"] == "source_failed" and receiver._file is None
    receiver.expect("new", "main", "sid")
    receiver.feed({"type": "source_end", **owner, "chunks": 0})
    assert not any(event["type"] == "source_ready" for event in published)
    receiver.close()


def test_claw_correlated_source_replacement_preserves_query_draft_and_closes_stale_owner():
    ui = ChatApp("/tmp/no-source.sock")
    try:
        ui._handle_response({"type": "pong", "session_id": "sid", "transcript_source": 1})
        ui._editor.set_text("draft")
        ui._open_transcript("needle")
        request = ui._send_queue.get_nowait()
        assert request["type"] == "source_read"
        assert ui._transcript_panel.selection is None
        record = TranscriptRecord("m", "tool", "result", "x" * 50000 + "needle")
        ui._handle_response(dict(type="source_ready", request_id="stale", session_id="sid",
                                 group_id="main", records=(record,)))
        assert ui._source_records is None
        ui._handle_response(dict(type="source_ready", request_id=request["request_id"], session_id="sid",
                                 group_id="main", records=(record,)))
        assert ui._source_records == (record,) and ui._editor.get_text() == "draft"
        assert ui._transcript_panel._request.query == "needle"
        ui._close_transcript()
        assert ui._source_records is None
        ui._handle_response(dict(type="source_ready", request_id=request["request_id"], session_id="sid",
                                 group_id="main", records=(record,)))
        assert ui._source_records is None and ui._transcript_panel is None
    finally:
        ui._request_exit()


def test_source_spool_worker_cancellation_releases_file():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ValueError, match="canceled"):
        prepare_source(SourceCapture("sid", lambda: [{"role": "user", "content": "body"}]), cancel)


async def test_source_requests_coalesce_and_do_not_block_gateway_controls(gateway):
    server, path, calls = gateway
    runtime = server._orchestrator._runtimes["main"]
    meta = runtime.transcript_store.create_session("main")
    runtime.transcript_store.replace_transcript(meta.session_id, [{"role": "user", "content": "source"}])
    from xdog.claw.core.runtime import source_protocol
    original = source_protocol.prepare_source
    entered = threading.Event()
    preparations = []
    def prepare(capture, canceled):
        preparations.append(capture)
        if len(preparations) == 1:
            entered.set()
            assert canceled.wait(5)
        return original(capture, canceled)
    reader, writer = await asyncio.open_unix_connection(path, limit=1_048_576)
    try:
        with patch.object(source_protocol, "prepare_source", side_effect=prepare):
            await send(writer, dict(type="source_read", request_id="old", group_id="main", session_id=meta.session_id))
            assert await asyncio.to_thread(entered.wait, 5)
            await send(writer, dict(type="ping"))
            assert (await read(reader, "pong"))["transcript_source"] == 1
            records = await source(reader, writer, meta.session_id, nonce="latest")
            assert records[0].body == "source" and len(preparations) == 2
        assert not calls
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize("damage", ["checksum", "revision", "incomplete", "duplicate"])
def test_receiver_rejects_corrupt_or_incomplete_transfer_and_releases_spool(damage):
    data = (json.dumps(asdict(TranscriptRecord("m", "assistant", "text", "complete"))) + "\n").encode()
    published = []
    receiver = SourceReceiver(published.append)
    owner = dict(request_id="read", group_id="main", session_id="sid", revision=2)
    receiver.expect("read", "main", "sid")
    receiver.feed(dict(type="source_begin", **owner, bytes=len(data),
                       sha256="0" * 64 if damage == "checksum" else hashlib.sha256(data).hexdigest()))
    if damage != "incomplete":
        receiver.feed({"type": "source_chunk", **owner, "revision": 3 if damage == "revision" else 2,
                       "index": 0, "data": base64.b64encode(data).decode()})
    if damage == "duplicate":
        receiver.feed(dict(type="source_chunk", **owner, index=0, data=base64.b64encode(data).decode()))
    receiver.feed(dict(type="source_end", **owner, chunks=1))
    assert published[-1]["type"] == "source_failed" and receiver._file is None
    assert not any(event["type"] == "source_ready" for event in published)
    receiver.close()


def test_unsolicited_source_reply_without_owned_request_is_ignored():
    ui = ChatApp("/tmp/unused-source.sock")
    try:
        ui._open_transcript()
        ui._handle_response(dict(type="source_ready", session_id="", group_id="main", records=()))
        assert ui._source_records is None
    finally:
        ui._request_exit()
