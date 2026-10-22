from __future__ import annotations

import runpy
from pathlib import Path

import httpx
import pytest
from xdog.agent.mcp import McpError, McpRegistry
from xdog.agent.mcp.http import _sse_messages, _SseState

ResumePeer = runpy.run_path(str(Path(__file__).parent / "fixtures" / "mcp_resume_peer.py"))["ResumePeer"]


def manager(peer, tmp_path, **options):
    return McpRegistry({"http": {"transport": "http", "url": peer.url, "timeout": 2, **options}}, tmp_path)


def calls(peer):
    return [request for request, _headers in peer.requests if request.get("method") == "tools/call"]


def resumed(peer):
    return [headers for headers in peer.gets if "Last-Event-ID" in headers]


async def test_sse_cursor_dispatch_empty_reset_nul_partial_cr_and_retry_bounds():
    state = _SseState()
    async def parse(raw):
        return [message async for message in _sse_messages(httpx.Response(200, content=raw), state)]
    assert await parse(b"id: first\rretry: 1\rdata:\r\r") == []
    assert state.cursor == "first" and state.retry == 0.05
    assert await parse(b"id: ignored\x00id\nretry: +1\n\n") == []
    assert state.cursor == "first" and state.retry == 0.05
    with pytest.raises(McpError, match="incomplete"):
        await parse(b'id: uncommitted\ndata: {"partial":')
    assert state.cursor == "first"
    assert await parse(b"id\nretry: 99999999999999999999999999\n\n") == []
    assert state.cursor == "" and state.retry == 5
    assert await parse(b'data: {"ok":true}\r\r') == [{"ok": True}]
    with pytest.raises(McpError, match="identifier"):
        await parse(b"id: unsafe\x01id\n\n")


@pytest.mark.parametrize("value", ["partial"])
async def test_legacy_post_stream_resumes_via_get_with_utf8_cursor_and_never_replays_call(tmp_path, value):
    with ResumePeer() as peer:
        client = manager(peer, tmp_path)
        try:
            await client.connect("http")
            result = await client.tools()[0].execute("call", {"value": value})
            assert result.content[0].text == f"RESUMED-{value}"
            assert len(calls(peer)) == 1 and len(resumed(peer)) == 1
            headers = resumed(peer)[0]
            expected = next(iter(peer.pending))
            assert headers["Last-Event-ID"].encode("latin-1") == expected.encode("utf-8")
            assert headers["Mcp-Session-Id"] == "resume-session"
            assert headers["MCP-Protocol-Version"] == "2025-11-25"
            assert headers["Accept"] == "text/event-stream"
        finally:
            await client.aclose()
        assert peer.deletes == 1
