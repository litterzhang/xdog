"""Owned loopback HTTP evidence, not independent SDK/vendor interoperability."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from xdog.agent.mcp import McpError, McpRegistry

from packages.agent.tests.test_mcp_elicitation import form

ROOTS = [{"uri": "file:///explicit/root", "name": "Explicit"}]
STATE = "  opaque\x00state/中文\n  "
METHODS = ["tools/call", "resources/read", "prompts/get"]


class SamplingHttpPeer:
    def __init__(self, mode, *, sse=False, task=False, family="sampling/createMessage"):
        self.mode, self.sse, self.task, self.family = mode, sse, task, family
        self.requests, self.gets, self.deletes = [], 0, 0
        peer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def respond(self, status, body=b"", *, media="application/json", headers=None):
                self.send_response(status)
                self.send_header("Content-Type", media)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.close_connection = True
                try:
                    for offset in range(0, len(body), 7):
                        self.wfile.write(body[offset:offset + 7])
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def result(self, identifier, value, *, headers=None):
                message = {"jsonrpc": "2.0", "id": identifier, "result": value}
                body = json.dumps(message, ensure_ascii=False).encode()
                if peer.sse:
                    self.respond(200, b"data: " + body + b"\r\n\r\n", media="text/event-stream", headers=headers)
                else:
                    self.respond(200, body, headers=headers)

            def do_GET(self):
                peer.gets += 1
                self.respond(405)

            def do_DELETE(self):
                peer.deletes += 1
                self.respond(204)

            def do_POST(self):
                row = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                peer.requests.append(row)
                method, identifier = row.get("method"), row.get("id")
                if method == "server/discover":
                    if peer.mode.startswith("legacy"):
                        self.respond(400)
                    else:
                        self.result(identifier, {"supportedVersions": ["2026-07-28"], "capabilities": {
                            "tools": {}, "resources": {}, "prompts": {}}})
                elif method == "initialize":
                    self.result(identifier, {"protocolVersion": "2025-11-25", "capabilities": {
                        "tools": {}, "resources": {}, "prompts": {}}}, headers={"Mcp-Session-Id": "owned"})
                elif method is None or identifier is None:
                    self.respond(202)
                elif method == "tools/list":
                    self.result(identifier, {"tools": [{"name": "echo", "inputSchema": {
                        "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
                        "outputSchema": {"type": "object", "properties": {"value": {"type": "string"}},
                                         "required": ["value"]}}]})
                elif method == "prompts/list":
                    names = ["retired", "safe", *(["roots"] if peer.mode == "retained" else [])]
                    self.result(identifier, {"prompts": [{"name": name} for name in names]})
                elif method in METHODS:
                    params = row["params"]
                    value = (params["arguments"]["value"] if method == "tools/call" else
                             params["uri"].removeprefix("test://") if method == "resources/read" else params["name"])
                    final = ({"content": [{"type": "text", "text": "RETAINED-" + value}],
                              "structuredContent": {"value": value}} if method == "tools/call" else
                             {"contents": [{"uri": params["uri"], "text": "RETAINED-" + value}]}
                             if method == "resources/read" else {"messages": [{"role": "user", "content": {
                                 "type": "text", "text": "RETAINED-" + value}}]})
                    if value == "safe":
                        self.result(identifier, final)
                        return
                    removed_params = (form()["params"] if peer.family == "elicitation/create"
                                      else {"messages": [], "maxTokens": 20})
                    if peer.task:
                        removed_params["task"] = {"ttl": 1000}
                    sampling = {"method": peer.family, "params": removed_params}
                    if peer.mode.startswith("legacy") or peer.mode == "modern-reverse":
                        reverse = {"jsonrpc": "2.0", "id": "retired-sampling", **sampling}
                        body = b"data: " + json.dumps(reverse).encode() + b"\r\n\r\n"
                        if peer.mode == "legacy-interrupted":
                            body += b'data: {"jsonrpc":"2.0"'  # No cursor or complete terminal result.
                        else:
                            body += b"data: " + json.dumps({"jsonrpc": "2.0", "id": identifier,
                                                          "result": final}).encode() + b"\r\n\r\n"
                        self.respond(200, body, media="text/event-stream")
                    else:
                        if "inputResponses" in params:
                            self.result(identifier, final)
                            return
                        if peer.mode == "retained":
                            inputs = {"review": {"method": "roots/list"}}
                        else:
                            inputs = {"first": {"method": "roots/list"}, "sampling": sampling}
                        self.result(identifier, {"resultType": "input_required", "inputRequests": inputs,
                                                 "requestState": STATE})
                else:
                    self.result(identifier, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        assert not self.thread.is_alive()


def make_manager(peer, tmp_path):
    return McpRegistry({"owned": {"transport": "http", "url": peer.url, "timeout": 2, "roots": ROOTS}}, tmp_path)


async def invoke(manager, method, value):
    if method == "tools/call":
        return await manager.tools()[0].execute("explicit", {"value": value})
    if method == "resources/read":
        return await manager.resource("owned", "test://" + value)
    return await manager.prompt("owned", value, {})


def declarations(peer):
    return [row["params"]["capabilities"] if row.get("method") == "initialize" else
            row.get("params", {}).get("_meta", {}).get("io.modelcontextprotocol/clientCapabilities", {})
            for row in peer.requests]


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("task", [False, True])
async def test_loopback_legacy_sampling_refused_once_without_callback_or_operation_replay(tmp_path, method, task):
    with SamplingHttpPeer("legacy", task=task) as peer:
        manager = make_manager(peer, tmp_path)
        try:
            await manager.connect("owned")
            result = await invoke(manager, method, "retired")
            assert "RETAINED-retired" in str(result)
            requests = [row for row in peer.requests if row.get("method") == method]
            replies = [row for row in peer.requests if row.get("id") == "retired-sampling"]
            assert len(requests) == 1
            assert replies == [{"jsonrpc": "2.0", "id": "retired-sampling", "error": {
                "code": -32601, "message": "Sampling is not supported"}}]
            assert all("sampling" not in json.dumps(row) for row in declarations(peer))
            client = manager._clients["owned"]
            assert not hasattr(client, "reverse") and not client.inputs._jobs
            assert "RETAINED-safe" in str(await invoke(manager, method, "safe"))
        finally:
            await manager.aclose()
        assert peer.deletes == 1 and not manager._thread.is_alive()


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("sse", [False, True])
@pytest.mark.parametrize("task", [False, True])
async def test_loopback_modern_sampling_rejects_entire_round_without_callback_or_resume(tmp_path, method, sse, task):
    with SamplingHttpPeer("modern", sse=sse, task=task) as peer:
        manager = make_manager(peer, tmp_path)
        try:
            await manager.connect("owned")
            with pytest.raises(McpError, match="task augmentation" if task else "not declared"):
                await invoke(manager, method, "retired")
            requests = [row for row in peer.requests if row.get("method") == method]
            assert len(requests) == 1 and "inputResponses" not in requests[0]["params"]
            assert not any(row.get("id") == "retired-sampling" for row in peer.requests)
            assert all("sampling" not in json.dumps(row) for row in declarations(peer))
            client = manager._clients["owned"]
            assert not client.inputs._jobs and not hasattr(client, "reverse")
            assert "RETAINED-safe" in str(await invoke(manager, method, "safe"))
            assert peer.gets == 0
        finally:
            await manager.aclose()
        assert peer.deletes == 0 and not manager._thread.is_alive()


@pytest.mark.parametrize("mode,error", [("modern-reverse", "unrelated"), ("legacy-interrupted", "no replay")])
async def test_loopback_sampling_stream_failure_never_reposts_side_effecting_call(tmp_path, mode, error):
    with SamplingHttpPeer(mode) as peer:
        manager = make_manager(peer, tmp_path)
        try:
            await manager.connect("owned")
            with pytest.raises(McpError, match=error):
                await invoke(manager, "tools/call", "retired")
            assert len([row for row in peer.requests if row.get("method") == "tools/call"]) == 1
            replies = [row for row in peer.requests if row.get("id") == "retired-sampling"]
            assert len(replies) == (1 if mode.startswith("legacy") else 0)
            assert not hasattr(manager._clients["owned"], "reverse")
            assert "RETAINED-safe" in str(await invoke(manager, "tools/call", "safe"))
            if mode == "modern-reverse":
                assert peer.gets == 0
        finally:
            await manager.aclose()


async def test_loopback_current_roots_retain_exact_explicit_continuation(tmp_path):
    with SamplingHttpPeer("retained") as peer:
        manager = make_manager(peer, tmp_path)
        try:
            await manager.connect("owned")
            assert "RETAINED-roots" in str(await invoke(manager, "resources/read", "roots"))
            requests = [row for row in peer.requests if row.get("method") == "resources/read"]
            assert len(requests) == 2 and requests[0]["id"] != requests[1]["id"]
            original, resumed = (row["params"] for row in requests)
            assert {key: value for key, value in resumed.items()
                    if key not in {"inputResponses", "requestState"}} == original
            assert resumed["requestState"] == STATE
            assert resumed["inputResponses"] == {"review": {"roots": ROOTS}}
            assert peer.gets == 0 and not manager._clients["owned"].inputs._jobs
        finally:
            await manager.aclose()
