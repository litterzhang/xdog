from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from xdog.agent.mcp import McpError, McpManager
from xdog.agent.mcp.config import parse_servers


@pytest.fixture
def peer():
    state = {"mode": "modern", "requests": [], "gets": 0, "deletes": 0, "headers": [], "calls": 0}
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args):
            pass
        def respond(self, status, value=None, *, media="application/json", extra=None):
            body = json.dumps(value).encode() if value is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(body)))
            for key, val in (extra or {}).items():
                self.send_header(key, val)
            self.end_headers()
            if body:
                self.wfile.write(body)
                self.wfile.flush()
        def sse(self, value):
            raw = json.dumps(value, ensure_ascii=False).encode()
            body = b": heartbeat\r\nid: event\r\nevent: message\r\ndata: " + raw + b"\r\n\r\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            # Deliberately split UTF-8/data boundaries; these are byte streams.
            for offset in range(0, len(body), 7):
                self.wfile.write(body[offset:offset + 7])
                self.wfile.flush()
        def do_GET(self):
            state["gets"] += 1
            self.respond(405)
        def do_DELETE(self):
            state["deletes"] += 1
            assert self.headers.get("Mcp-Session-Id") == "owned-session"
            self.respond(204)
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append(request)
            state["headers"].append(dict(self.headers))
            method, identifier = request.get("method"), request.get("id")
            mode = state["mode"]
            if mode == "auth" and self.headers.get("Authorization") != "Bearer configured-secret":
                self.respond(401, {"error": "do-not-display-credential"})
                return
            if mode == "redirect":
                self.respond(307, extra={"Location": "http://127.0.0.1:9/not-forwarded"})
                return
            if method == "server/discover":
                if mode == "legacy":
                    self.respond(400)
                elif mode == "reject":
                    self.respond(400, {"jsonrpc": "2.0", "id": identifier,
                                      "error": {"code": -32022, "message": "Unsupported version"}})
                else:
                    self.respond(200, {"jsonrpc": "2.0", "id": identifier,
                                       "result": {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}},
                                            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "fixture"}}}})
            elif method == "initialize":
                self.respond(200, {"jsonrpc": "2.0", "id": identifier,
                                   "result": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}}},
                             extra={"Mcp-Session-Id": "owned-session"})
            elif identifier is None or method is None:
                self.respond(202)
            elif method == "tools/list":
                schema = {"type": "object", "required": ["value"], "properties": {
                    "value": {"type": "string", "x-mcp-header": "Value"}}}
                bad = {"type": "object", "properties": {"array": {"type": "array", "x-mcp-header": "Bad"}}}
                self.respond(200, {"jsonrpc": "2.0", "id": identifier, "result": {"tools": [
                    {"name": "echo.remote", "inputSchema": schema}, {"name": "reject.header", "inputSchema": bad}]}})
            elif method == "tools/call":
                state["calls"] += 1
                value = request["params"]["arguments"]["value"]
                if value == "expired":
                    self.respond(404)
                    return
                if value == "delay":
                    time.sleep(0.7)
                message = {"jsonrpc": "2.0", "id": identifier,
                           "result": {"content": [{"type": "text", "text": value}]}}
                if value == "mismatch":
                    message["id"] = 999999
                if mode == "sse":
                    self.sse(message)
                else:
                    try:
                        self.respond(200, message)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}/mcp"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def manager(url, tmp_path, **options):
    return McpManager({"http": {"transport": "http", "url": url, "timeout": 2, **options}}, tmp_path)


@pytest.mark.parametrize("mode", ["modern", "sse", "legacy"])
async def test_real_http_negotiation_tools_mirrors_streaming_session_and_close(peer, tmp_path, mode):
    state, url = peer
    state["mode"] = mode
    client = manager(url, tmp_path)
    assert not state["requests"]
    try:
        await client.connect("http")
        tools = client.tools()
        assert len(tools) == (2 if mode == "legacy" else 1)
        result = await tools[0].execute("echo", {"value": " spaced e\u0301界\r\n "})
        assert result.content[0].text == " spaced e\u0301界\r\n "
        if mode != "legacy":
            assert client.status()[0]["rejected_tools"] == ("reject.header",)
            request, headers = state["requests"][-1], state["headers"][-1]
            assert headers["Mcp-Method"] == "tools/call" and headers["Mcp-Name"] == "echo.remote"
            encoded = "=?base64?" + base64.b64encode(result.content[0].text.encode()).decode() + "?="
            assert headers["Mcp-Param-Value"] == encoded
            assert request["params"]["arguments"]["value"] == result.content[0].text
            assert headers["MCP-Protocol-Version"] == "2026-07-28"
            assert not any(value.get("method") == "initialize" for value in state["requests"])
            assert state["gets"] == 0 and state["deletes"] == 0
        else:
            assert state["headers"][-1]["Mcp-Session-Id"] == "owned-session"
            with pytest.raises(McpError, match="expired"):
                await tools[0].execute("expired", {"value": "expired"})
            assert sum(value.get("method") == "tools/call" and value["params"]["arguments"].get("value") == "expired"
                       for value in state["requests"]) == 1
    finally:
        await client.aclose()
    if mode == "legacy":
        assert state["deletes"] == 1
    assert not client._thread.is_alive()


@pytest.mark.parametrize("mode", ["reject", "auth", "redirect"])
async def test_negotiation_auth_and_redirect_failures_do_not_downgrade_or_forward(peer, tmp_path, mode):
    state, url = peer
    state["mode"] = mode
    client = manager(url, tmp_path)
    try:
        with pytest.raises(McpError) as caught:
            await client.connect("http")
        assert "do-not-display-credential" not in str(caught.value)
        assert len(state["requests"]) == 1
        assert not any(value.get("method") == "initialize" for value in state["requests"])
    finally:
        await client.aclose()


async def test_explicit_env_credentials_and_timeout_cancel_identity_fences(peer, tmp_path, monkeypatch):
    state, url = peer
    state["mode"] = "auth"
    monkeypatch.setenv("MCP_HTTP_AUTH", "Bearer configured-secret")
    client = manager(url, tmp_path, headers_env={"Authorization": "MCP_HTTP_AUTH"}, timeout=0.2)
    try:
        await client.connect("http")
        tool = client.tools()[0]
        assert "configured-secret" not in str(client.status())
        with pytest.raises(McpError, match="not replayed"):
            await tool.execute("delay", {"value": "delay"})
        assert state["calls"] == 1
        assert (await tool.execute("next", {"value": "next"})).content[0].text == "next"
        with pytest.raises(McpError, match="identity"):
            await tool.execute("mismatch", {"value": "mismatch"})
        assert state["calls"] == 3
    finally:
        await client.aclose()


@pytest.mark.parametrize("bad", [
    {"url": "ftp://localhost/mcp"}, {"url": "http://remote.example/mcp"},
    {"url": "https://user:secret@example.com/mcp"}, {"headers": {"Mcp-Method": "override"}},
    {"headers": {"Authorization": "Bearer secret\r\nInjected: yes"}},
])
def test_http_configuration_rejects_unsafe_or_reserved_fields(tmp_path, bad):
    with pytest.raises(ValueError):
        parse_servers({"http": {"transport": "http", "url": "https://example.com/mcp", **bad}})


def test_header_annotations_paths_duplicates_composition_and_encoding():
    from xdog.agent.mcp.protocol import argument_headers, header_value, schema_headers

    schema = {"type": "object", "properties": {"nested": {"type": "object", "properties": {
        "flag": {"type": "boolean", "x-mcp-header": "Flag"},
        "number": {"type": "integer", "x-mcp-header": "Count"}}}}}
    paths = schema_headers(schema)
    assert argument_headers(paths, {"nested": {"flag": True, "number": 2}}) == {
        "Mcp-Param-Flag": "true", "Mcp-Param-Count": "2"}
    assert not argument_headers(paths, {})
    with pytest.raises(McpError, match="range"):
        argument_headers(paths, {"nested": {"number": 2**54}})
    assert header_value("=?base64?ambiguous?=").startswith("=?base64?")
    assert header_value("ascii value") == "ascii value"
    invalid = [
        {"type": "string", "x-mcp-header": "Root"},
        {"type": "object", "allOf": [{"properties": {"x": {"type": "string", "x-mcp-header": "X"}}}]},
        {"type": "object", "properties": {"x": {"type": ["string", "null"], "x-mcp-header": "X"}}},
        {"type": "object", "properties": {"x": {"type": "string", "x-mcp-header": "Foo"},
                                          "y": {"type": "integer", "x-mcp-header": "foo"}}},
    ]
    for value in invalid:
        with pytest.raises(McpError, match="annotation"):
            schema_headers(value)


async def test_cancelled_http_call_closes_owned_stream_and_does_not_emit_modern_notification(peer, tmp_path):
    state, url = peer
    client = manager(url, tmp_path)
    try:
        await client.connect("http")
        cancel = asyncio.Event()
        task = asyncio.create_task(client.tools()[0].execute("cancel", {"value": "delay"}, cancel))
        deadline = asyncio.get_running_loop().time() + 3
        while state["calls"] == 0 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.005)
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert state["calls"] == 1
        assert not any(value.get("method") == "notifications/cancelled" for value in state["requests"])
        result = await client.tools()[0].execute("next", {"value": "still connected"})
        assert result.content[0].text == "still connected"
    finally:
        await client.aclose()


async def test_bounded_sse_decoder_multiline_partial_and_large_events():
    import httpx
    from xdog.agent.mcp import http as module

    async def collect(raw):
        response = httpx.Response(200, content=raw)
        return [item async for item in module._sse_messages(response)]
    assert await collect(b'data: {"jsonrpc":"2.0",\ndata: "id":1,"result":{}}\n\n') == [
        {"jsonrpc": "2.0", "id": 1, "result": {}}]
    with pytest.raises(McpError, match="incomplete"):
        await collect(b'data: {"id":1}')
    with pytest.raises(McpError, match="limit"):
        await collect(b"data: " + b"x" * (module.MAX_FRAME + 1))
