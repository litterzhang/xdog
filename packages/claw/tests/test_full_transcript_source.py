from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
from dataclasses import asdict
from unittest.mock import patch

import pytest
from xdog.agent import AgentTool, AgentToolResult
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
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


async def test_persisted_source_exceeds_all_old_caps_redacts_and_omits_binary(gateway):
    server, path, calls = gateway
    runtime = server._orchestrator._runtimes["main"]
    meta = runtime.transcript_store.create_session("main")
    text = "\\\"界" * (400 * 1024) + "LARGE-RECORD-END password=secret-text"
    content = [{"type": "text", "text": f"part-{i}"} for i in range(20)]
    content += [{"type": "thinking", "thinking": "REASONING-AFTER-PART-CAP"},
                {"type": "thinking", "thinking": "HIDDEN-REDACTED", "redacted": True},
                {"type": "toolCall", "id": "call", "name": "bash", "arguments": {
                    "command": "x" * 5000 + "COMMAND-AFTER-CAP", "password": "secret-value",
                    "items": [f"array-{i}" for i in range(60)]}}]
    runtime.transcript_store.replace_transcript(meta.session_id, [
        {"role": "user", "content": [{"type": "text", "text": "inspect"},
                                      {"type": "image", "data": "DO-NOT-EXPOSE-BINARY", "mime_type": "image/png"}]},
        {"role": "user", "content": "HIDDEN-GOAL", "channel": "goal_runner"},
        {"role": "assistant", "content": content},
        {"role": "toolResult", "tool_call_id": "call", "tool_name": "bash",
         "content": [{"type": "text", "text": text}]},
    ])
    reader, writer = await asyncio.open_unix_connection(path, limit=1_048_576)
    try:
        await send(writer, {"type": "ping"})
        pong = await read(reader, "pong")
        assert pong["transcript_source"] == 1
        # Compatibility projection is still bounded. New source is not.
        assert "LARGE-RECORD-END" not in json.dumps(pong)
        records = await source(reader, writer, meta.session_id)
        body = "\n".join(record.body for record in records)
        assert all(marker in body for marker in ("LARGE-RECORD-END", "COMMAND-AFTER-CAP", "array-59",
                                                "part-19", "REASONING-AFTER-PART-CAP"))
        assert not any(value in body for value in ("secret-text", "secret-value", "DO-NOT-EXPOSE-BINARY",
                                                   "HIDDEN-REDACTED", "HIDDEN-GOAL"))
        assert "<redacted>" in body and "[Images: 1]" in body
        assert not calls
    finally:
        writer.close()
        await writer.wait_closed()


async def test_live_sdk_tool_progress_arguments_and_completed_source(gateway):
    server, path, calls = gateway
    entered, release = asyncio.Event(), asyncio.Event()
    runtime = server._orchestrator._runtimes["main"]
    args = {"command": "x" * 5000 + "LIVE-ARG-END", "items": [str(i) for i in range(60)]}
    async def execute(tool_id, arguments, cancel=None, on_update=None, **kwargs):
        await on_update(AgentToolResult(content=(TextContent(text="p" * 50000 + "LIVE-PROGRESS-END"),)))
        entered.set()
        await release.wait()
        return AgentToolResult(content=(TextContent(text="f" * 200000 + "LIVE-FINAL-END"),))
    runtime._tools = [AgentTool(name="source_tool", description="fixture",
                               parameters={"type": "object"}, execute=execute)]
    def stream_fn(model, context, options):
        calls.append(context)
        message = (AssistantMessage(content=(ToolCall(id="live", name="source_tool", arguments=args),),
                                    stop_reason="toolUse")
                   if context.messages[-1].role == "user" else AssistantMessage(content=(TextContent(text="done"),)))
        async def generate():
            yield DoneEvent(message=message, stop_reason=message.stop_reason)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)
    runtime.stream_fn = stream_fn
    reader, writer = await asyncio.open_unix_connection(path, limit=1_048_576)
    try:
        await send(writer, dict(type="message", run_id="run", content="tool please"))
        ack = await read(reader, "run_ack")
        await asyncio.wait_for(entered.wait(), 5)
        partial = await source(reader, writer, ack["session_id"])
        assert any("LIVE-PROGRESS-END" in record.body and "LIVE-ARG-END" in record.body for record in partial)
        assert len(calls) == 1 and runtime._active_session.agent.state.is_streaming
        release.set()
        await read(reader, "response")
        complete = await source(reader, writer, ack["session_id"], nonce="complete")
        assert any("LIVE-FINAL-END" in record.body for record in complete)
        assert len(calls) == 2
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()


async def test_foreign_session_unknown_group_and_same_session_navigation_fence_source(gateway):
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
            # Same session ID, different head. No source may be committed under
            # the old revision even though an ID-only guard would accept it.
            await send(peer_writer, dict(type="session_control", request_id="goto", action="goto", group_id="main",
                                         session_id=meta.session_id, index=0, expected_session=meta.session_id))
            assert (await read(peer_reader, "session_result"))["success"]
            await read(peer_reader, "session_changed")
            release.set()
            assert "changed" in (await read(reader, "source_error"))["error"].lower()
        assert len(await source(reader, writer, meta.session_id, nonce="fresh")) == 1
        assert not calls
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


async def test_real_client_connection_reads_full_source_off_ui_and_retains_query(gateway):
    server, path, calls = gateway
    runtime = server._orchestrator._runtimes["main"]
    meta = runtime.transcript_store.create_session("main")
    runtime.transcript_store.replace_transcript(meta.session_id, [
        {"role": "user", "content": "context"},
        {"role": "assistant", "content": [{"type": "text", "text": "x" * 200000 + "CLIENT-AFTER-CAP"}]},
    ])
    ui = ChatApp(path)
    ui._io_generation = 1
    ui._handle_response({"type": "_connection_opened", "generation": 1})
    reader, writer = await asyncio.open_unix_connection(path, limit=4 * 1024 * 1024)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait_for(predicate):
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            ui._poll()
            if predicate():
                return
            await asyncio.sleep(0.005)
        raise AssertionError("Client source transaction did not settle")
    try:
        # The production transport owns its actual ping/history handshake.
        await wait_for(lambda: ui._connection_ready)
        ui._editor.set_text("keep draft")
        ui._open_transcript("CLIENT-AFTER-CAP")
        await wait_for(lambda: ui._source_records is not None and not ui._transcript_panel.browser.snapshot().busy)
        ui._poll()
        assert any("CLIENT-AFTER-CAP" in record.body for record in ui._source_records)
        assert ui._transcript_panel._view.matches == 1 and ui._editor.get_text() == "keep draft"
        assert not ui._transcript_panel._source_busy and not calls
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()


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
