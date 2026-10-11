from __future__ import annotations

import asyncio
import runpy
from pathlib import Path

import httpx
import pytest
from xdog.agent.mcp import McpError, McpManager
from xdog.agent.mcp.http import MAX_RESUME_ATTEMPTS, _sse_messages, _SseState

ResumePeer = runpy.run_path(str(Path(__file__).parent / "fixtures" / "mcp_resume_peer.py"))["ResumePeer"]


def manager(peer, tmp_path, **options):
    return McpManager({"http": {"transport": "http", "url": peer.url, "timeout": 2, **options}}, tmp_path)


def calls(peer):
    return [request for request, _headers in peer.requests if request.get("method") == "tools/call"]


def resumed(peer):
    return [headers for headers in peer.gets if "Last-Event-ID" in headers]


@pytest.mark.parametrize("value", ["success", "partial", "broken"])
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


async def test_simultaneous_calls_keep_independent_cursor_and_response_identity(tmp_path):
    with ResumePeer() as peer:
        client = manager(peer, tmp_path)
        try:
            await client.connect("http")
            tool = client.tools()[0]
            first, second = await asyncio.gather(tool.execute("one", {"value": "first"}),
                                                 tool.execute("two", {"value": "second"}))
            assert first.content[0].text == "RESUMED-first" and second.content[0].text == "RESUMED-second"
            assert len(calls(peer)) == len(resumed(peer)) == 2
            assert len({headers["Last-Event-ID"] for headers in resumed(peer)}) == 2
        finally:
            await client.aclose()


@pytest.mark.parametrize("value,gets,match", [
    ("noid", 0, "no replay"), ("emptyid", 0, "no replay"), ("invalid-json", 0, "JSON"),
    ("auth", 1, "authorization"), ("redirect", 1, "redirect"), ("expired", 1, "expired"),
    ("unavailable", 1, "unavailable"), ("mismatch", 1, "identity"), ("reset", 1, "exhausted"),
    ("exhaust", MAX_RESUME_ATTEMPTS, "exhausted"),
])
async def test_missing_reset_invalid_or_rejected_resumption_never_replays_or_reconnects(tmp_path, value, gets, match):
    with ResumePeer() as peer:
        client = manager(peer, tmp_path)
        try:
            await client.connect("http")
            with pytest.raises(McpError, match=match) as caught:
                await client.tools()[0].execute("call", {"value": value})
            assert "do-not-leak-token" not in str(caught.value)
            assert len(calls(peer)) == 1 and len(resumed(peer)) == gets
            assert sum(request.get("method") == "initialize" for request, _headers in peer.requests) == 1
        finally:
            await client.aclose()


async def test_resumption_shares_original_absolute_timeout(tmp_path):
    with ResumePeer() as peer:
        client = manager(peer, tmp_path, timeout=0.2)
        try:
            await client.connect("http")
            with pytest.raises(McpError, match="timed out"):
                await client.tools()[0].execute("call", {"value": "timeout"})
            assert peer.resume_started.is_set() and len(calls(peer)) == len(resumed(peer)) == 1
        finally:
            await client.aclose()


@pytest.mark.parametrize("close", [False, True])
async def test_cancel_or_disconnect_during_resume_closes_owned_read_without_new_call(tmp_path, close):
    with ResumePeer() as peer:
        client = manager(peer, tmp_path)
        try:
            await client.connect("http")
            cancel = asyncio.Event()
            task = asyncio.create_task(client.tools()[0].execute("call", {"value": "cancel"}, cancel))
            async with asyncio.timeout(3):
                while not peer.resume_started.is_set():
                    await asyncio.sleep(0.002)
            if close:
                await client.disconnect("http")
            else:
                cancel.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(calls(peer)) == len(resumed(peer)) == 1
            if close:
                assert not client._clients
            else:
                async with asyncio.timeout(3):
                    while client._clients["http"]._active:
                        await asyncio.sleep(0.002)
        finally:
            await client.aclose()


async def test_notification_stream_resumes_only_its_cursor_and_reports_terminal_failure(tmp_path):
    with ResumePeer(notifications=True) as peer:
        client = manager(peer, tmp_path)
        try:
            await client.connect("http")
            peer.release.set()
            async with asyncio.timeout(3):
                while not client.status()[0]["error"]:
                    await asyncio.sleep(0.002)
            assert len(resumed(peer)) == 1 and resumed(peer)[0]["Last-Event-ID"] == "notification-1"
            assert not calls(peer) and client.status()[0]["changed"]
            assert "resumption" in client.status()[0]["error"]
        finally:
            await client.aclose()


async def test_modern_stateless_stream_never_uses_get_but_accepts_request_scoped_logs(tmp_path):
    with ResumePeer(modern=True) as peer:
        client = manager(peer, tmp_path)
        try:
            await client.connect("http")
            with pytest.raises(McpError, match="no replay"):
                await client.tools()[0].execute("incomplete", {"value": "success"})
            result = await client.tools()[0].execute("log", {"value": "current-log"})
            assert result.content[0].text == "CURRENT-LOG-RESULT"
            assert not peer.gets and len(calls(peer)) == 2
        finally:
            await client.aclose()


async def test_current_context_read_uses_mcp_name_uri_mirror_not_invented_header(tmp_path):
    with ResumePeer(modern=True) as peer:
        client = manager(peer, tmp_path)
        try:
            await client.connect("http")
            assert (await client.resource("http", "fixture://text")).text.endswith("resource")
            request, headers = peer.requests[-1]
            assert headers["Mcp-Name"] == request["params"]["uri"] == "fixture://text"
            assert "Mcp-Uri" not in headers
            await client.prompt("http", "review", {})
            assert peer.requests[-1][1]["Mcp-Name"] == "review"
        finally:
            await client.aclose()


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


async def test_legacy_initialize_response_resumes_before_negotiation_finishes_without_reinitialize(tmp_path):
    with ResumePeer(initialize_resume=True) as peer:
        client = manager(peer, tmp_path)
        try:
            await client.connect("http")
            assert client.status()[0]["state"] == "connected" and client.status()[0]["protocol"] == "2025-11-25"
            assert sum(request.get("method") == "initialize" for request, _headers in peer.requests) == 1
            assert len(resumed(peer)) == 1
            assert resumed(peer)[0]["Last-Event-ID"].startswith("initialize-")
            assert resumed(peer)[0]["MCP-Protocol-Version"] == "2025-11-25"
            assert resumed(peer)[0]["Mcp-Session-Id"] == "resume-session"
            assert "Mcp-Method" not in resumed(peer)[0]
            assert not calls(peer)
        finally:
            await client.aclose()
