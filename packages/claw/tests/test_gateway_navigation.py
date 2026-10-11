from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer


@pytest.fixture
async def gateway(tmp_path):
    config = ClawConfig(model="test/dummy", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "gateway.sock"), pid_file=str(tmp_path / "gateway.pid"),
                        groups=(GroupDef(id="main", is_main=True, workspace=str(tmp_path / "workspace")),))
    server = GatewayServer(config)
    calls = []

    def stream_fn(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/dummy", stream_fn)):
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
            value = json.loads(raw)
            if value["type"] == kind:
                return value
    return await asyncio.wait_for(until(), 3)


async def mutate(reader, writer, action, *, expected="", session="", branch="", index=-1, nonce="op"):
    await send(writer, dict(type="session_control", group_id="main", request_id=nonce, action=action,
                           expected_session=expected, session_id=session, branch_id=branch, index=index))
    result = await read(reader, "session_result")
    if not result["success"]:
        return result, None
    changed = await read(reader, "session_changed")
    entries = []
    if changed["history_count"]:
        while True:
            value = json.loads(await asyncio.wait_for(reader.readline(), 3))
            if value["type"] == "history_end":
                break
            assert value["type"] == "history_chunk"
            entries.extend(value["entries"])
    return changed, entries


async def test_new_archive_search_resume_tree_and_checkpoint_are_gateway_owned(gateway):
    server, path, calls = gateway
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        await send(writer, {"type": "ping"})
        assert (await read(reader, "pong"))["session_controls"] == 1
        first, _ = await mutate(reader, writer, "new")
        first_id = first["session_id"]
        runtime = server._orchestrator._runtimes["main"]
        runtime.transcript_store.replace_transcript(first_id, [
            {"role": "user", "content": "SEARCH-TARGET"},
            {"role": "assistant", "content": [{"type": "text", "text": "ANSWER"}]},
        ])
        second, _ = await mutate(reader, writer, "new", expected=first_id, nonce="new2")
        second_id = second["session_id"]
        await send(writer, dict(type="session_control", group_id="main", request_id="list", action="list",
                               scope="sessions", query=first_id[:10], offset=0))
        listing = await read(reader, "session_result")
        assert listing["success"] and listing["total"] == 1
        assert listing["entries"][0]["session_id"] == first_id
        resumed, history = await mutate(reader, writer, "resume", session=first_id, expected=second_id, nonce="resume")
        assert resumed["session_id"] == first_id and len(history) == 2
        await send(writer, dict(type="session_control", group_id="main", request_id="tree", action="tree",
                               scope=f"tree|{first_id}|", query="", offset=0))
        tree = await read(reader, "session_result")
        assert len(tree["entries"]) == 2
        moved, history = await mutate(
            reader, writer, "goto", session=first_id, expected=first_id, index=0, nonce="point",
        )
        assert moved["session_id"] == first_id and len(history) == 1
        assert len(runtime.transcript_store.branch_metadata(first_id)) == 2
        assert not calls  # Navigation does not invoke a model or tool loop.
    finally:
        writer.close()
        await writer.wait_closed()


async def test_busy_group_stale_expected_session_and_invalid_ids_leave_state_unchanged(gateway):
    server, path, calls = gateway
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        initial, _ = await mutate(reader, writer, "new")
        current = initial["session_id"]
        bad, _ = await mutate(reader, writer, "new", expected="stale", nonce="stale")
        assert not bad["success"]
        assert server._orchestrator.try_reserve_group("main")
        busy, _ = await mutate(reader, writer, "new", expected=current, nonce="busy")
        assert not busy["success"] and "idle" in busy["error"]
        server._orchestrator.release_group_reservation("main")
        malformed, _ = await mutate(reader, writer, "resume", session="../../outside", expected=current, nonce="bad")
        assert not malformed["success"]
        assert server._orchestrator.get_session_info("main")["session_id"] == current
        assert not calls
    finally:
        writer.close()
        await writer.wait_closed()
