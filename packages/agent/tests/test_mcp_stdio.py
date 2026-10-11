from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from xdog.agent.mcp import McpError, McpManager

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_server.py"


def manager(tmp_path, mode="modern", timeout=2):
    return McpManager({"fixture": {"command": sys.executable, "args": [str(FIXTURE), mode, str(tmp_path / "requests")],
                                    "timeout": timeout}}, tmp_path)


def logged(tmp_path):
    return [json.loads(line) for line in (tmp_path / "requests").read_text().splitlines()]


@pytest.mark.parametrize("mode", ["modern", "legacy"])
async def test_real_stdio_negotiation_discovery_schema_call_and_cleanup(tmp_path, mode):
    client = manager(tmp_path, mode)
    assert client._thread is None and not (tmp_path / "requests").exists()
    try:
        await client.connect("fixture")
        status = client.status()[0]
        assert status["protocol"] == ("2026-07-28" if mode == "modern" else "2025-11-25")
        tools = client.tools()
        assert len(tools) == 2 and all(tool.name.startswith("mcp_fixture_") for tool in tools)
        assert len(tools[0].name) <= 64
        result = await tools[0].execute("call", {"value": "actual bytes e\u0301界"})
        assert result.content[0].text == "actual bytes e\u0301界"
        structured = await tools[1].execute("call", {"count": 3})
        assert structured.details["structured_content"] == {"count": 3}
        before = len(logged(tmp_path))
        with pytest.raises(McpError, match="arguments"):
            await tools[0].execute("bad", {"value": "valid", "extra": True})
        assert len(logged(tmp_path)) == before
        failed = await tools[0].execute("bad", {"value": "error"})
        assert failed.is_error and failed.content  # Deliberate errors retain typed result data.
        with pytest.raises(McpError, match="structured output"):
            await tools[1].execute("bad", {"count": "wrong"})
        frames = logged(tmp_path)
        assert frames[0]["method"] == "server/discover"
        if mode == "modern":
            assert all("_meta" in frame["params"] for frame in frames if "id" in frame)
            assert not any(frame["method"] == "initialize" for frame in frames)
        else:
            assert any(frame["method"] == "notifications/initialized" for frame in frames)
        process = client._clients["fixture"].process
        await client.disconnect("fixture")
        assert process.returncode is not None and not client.tools()
    finally:
        await client.aclose()
    assert not client._thread.is_alive()


@pytest.mark.parametrize("mode", ["reject", "external-schema"])
async def test_rejected_negotiation_or_external_schema_is_not_fetched_or_fallback(tmp_path, mode):
    client = manager(tmp_path, mode)
    try:
        with pytest.raises(McpError):
            await client.connect("fixture")
        assert not client.tools() and not client._clients
        if mode == "reject":
            assert not any(frame["method"] == "initialize" for frame in logged(tmp_path))
    finally:
        await client.aclose()


async def test_timeout_late_response_and_explicit_cancel_do_not_replay_calls(tmp_path):
    client = manager(tmp_path, "legacy", timeout=0.2)
    try:
        await client.connect("fixture")
        tool = client.tools()[0]
        with pytest.raises(TimeoutError):
            await tool.execute("slow", {"value": "delay"})
        assert (await tool.execute("next", {"value": "success"})).content[0].text == "success"
        cancel = asyncio.Event()
        task = asyncio.create_task(tool.execute("cancel", {"value": "delay"}, cancel))
        await asyncio.sleep(0.05)
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.7)
        assert (await tool.execute("next2", {"value": "still owned"})).content[0].text == "still owned"
        frames = logged(tmp_path)
        delayed = [frame for frame in frames if frame["method"] == "tools/call"
                   and frame["params"]["arguments"].get("value") == "delay"]
        assert len(delayed) == 2
        assert any(frame["method"] == "notifications/cancelled" for frame in frames)
    finally:
        await client.aclose()


@pytest.mark.parametrize("failure", ["eof", "malformed"])
async def test_connection_failure_disables_existing_proxy_without_automatic_restart(tmp_path, failure):
    client = manager(tmp_path)
    try:
        await client.connect("fixture")
        tool = client.tools()[0]
        with pytest.raises(McpError, match="uncertain"):
            await tool.execute("broken", {"value": failure})
        with pytest.raises(McpError, match="not connected"):
            await tool.execute("no replay", {"value": "should not send"})
        assert sum(frame["method"] == "server/discover" for frame in logged(tmp_path)) == 1
    finally:
        await client.aclose()


def test_owner_loop_survives_coding_style_per_turn_event_loops(tmp_path):
    client = manager(tmp_path)
    try:
        asyncio.run(client.connect("fixture"))
        tool = client.tools()[0]
        assert asyncio.run(tool.execute("one", {"value": "first"})).content[0].text == "first"
        assert asyncio.run(tool.execute("two", {"value": "second"})).content[0].text == "second"
    finally:
        asyncio.run(client.aclose())


async def test_retired_connection_close_does_not_remove_reloaded_client_or_tools(tmp_path):
    from unittest.mock import AsyncMock, Mock
    client = McpManager({}, tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def close():
        entered.set()
        await release.wait()
    old = Mock(close=AsyncMock(side_effect=close))
    new = Mock()
    tool = Mock(server='fixture')
    client._clients['fixture'] = old
    task = asyncio.create_task(client._disconnect('fixture'))
    await entered.wait()
    client._clients['fixture'] = new
    client._tools = (tool,)
    client._rejected['fixture'] = ('new-worker-schema',)
    release.set()
    await task
    assert client._clients['fixture'] is new
    assert client._tools == (tool,) and client._rejected['fixture'] == ('new-worker-schema',)
    await client.aclose()
