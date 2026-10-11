"""Independent local HTTP peer: cursor replay uses GET, never another tool POST."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ResumePeer:
    def __init__(self, *, modern: bool = False, notifications: bool = False, initialize_resume: bool = False) -> None:
        self.modern, self.notifications = modern, notifications
        self.initialize_resume = initialize_resume
        self.requests: list[tuple[dict[str, Any], dict[str, str]]] = []
        self.gets: list[dict[str, str]] = []
        self.pending: dict[str, tuple[int, str]] = {}
        self.resume_started = threading.Event()
        self.release = threading.Event()
        self.deletes = 0
        peer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def respond(self, status: int, body: bytes = b"", *, sse: bool = False,
                        extra: dict[str, str] | None = None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream" if sse else "application/json")
                self.send_header("Content-Length", str(len(body)))
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                try:
                    # Byte-split UTF-8 identifiers, JSON and CRLF delimiters.
                    for offset in range(0, len(body), 11):
                        self.wfile.write(body[offset:offset + 11])
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def result(self, identifier: int, result: dict[str, Any], *, extra: dict[str, str] | None = None) -> None:
                self.respond(200, json.dumps({"jsonrpc": "2.0", "id": identifier, "result": result}).encode(),
                             extra=extra)

            def do_POST(self) -> None:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                peer.requests.append((request, dict(self.headers)))
                method, identifier = request.get("method"), request.get("id")
                if method == "server/discover":
                    if not peer.modern:
                        self.respond(400)
                    else:
                        self.result(identifier, {"supportedVersions": ["2026-07-28"],
                                                "capabilities": {"tools": {}, "resources": {}, "prompts": {}}})
                elif method == "initialize":
                    if peer.initialize_resume:
                        cursor = f"initialize-{identifier}"
                        peer.pending[cursor] = identifier, "initialize"
                        self.respond(200, f"id: {cursor}\nretry: 1\ndata:\n\n".encode(), sse=True,
                                     extra={"Mcp-Session-Id": "resume-session"})
                    else:
                        self.result(identifier, {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}},
                                    extra={"Mcp-Session-Id": "resume-session"})
                elif identifier is None or method is None:
                    self.respond(202)
                elif method == "tools/list":
                    self.result(identifier, {"tools": [{"name": "resume", "inputSchema": {
                        "type": "object", "properties": {"value": {"type": "string"}}}}]})
                elif method == "resources/read":
                    self.result(identifier, {"contents": [{"uri": request["params"]["uri"], "text": "resource"}]})
                elif method == "prompts/list":
                    self.result(identifier, {"prompts": [{"name": "review"}]})
                elif method == "prompts/get":
                    self.result(identifier, {"messages": [{"role": "user",
                                         "content": {"type": "text", "text": "review"}}]})
                elif method == "tools/call":
                    value = request["params"]["arguments"].get("value", "terminal")
                    cursor = f"post-{identifier}-界"
                    peer.pending[cursor] = identifier, value
                    priming = f"id: {cursor}\r\nretry: 1\r\ndata:\r\n\r\n".encode()
                    if value == "noid":
                        priming = b": no resumable cursor\n\n"
                    elif value == "emptyid":
                        priming += b"id:\n\ndata:\n\n"
                    elif value == "invalid-json":
                        priming += b"data: {invalid}\n\n"
                    elif value == "partial":
                        priming += b'id: uncommitted\ndata: {"jsonrpc":"2.0"'
                    elif value == "current-log":
                        log = {"jsonrpc": "2.0", "method": "notifications/message",
                               "params": {"level": "info", "data": "request-scoped log"}}
                        result = {"jsonrpc": "2.0", "id": identifier,
                                  "result": {"content": [{"type": "text", "text": "CURRENT-LOG-RESULT"}]}}
                        priming += b"data: " + json.dumps(log).encode() + b"\n\n"
                        priming += b"data: " + json.dumps(result).encode() + b"\n\n"
                    if value == "broken":
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Content-Length", str(len(priming) + 100))
                        self.end_headers()
                        self.wfile.write(priming)
                        self.wfile.flush()
                        self.close_connection = True  # Actual truncated HTTP body, not clean SSE EOF.
                    else:
                        self.respond(200, priming, sse=True)

            def do_GET(self) -> None:
                headers = dict(self.headers)
                peer.gets.append(headers)
                cursor = headers.get("Last-Event-ID", "").encode("latin-1").decode("utf-8")
                if not cursor:
                    if not peer.notifications:
                        self.respond(405)
                    else:
                        self.respond(200, b"id: notification-1\nretry: 1\ndata:\n\n", sse=True)
                    return
                if cursor.startswith("notification-"):
                    peer.release.wait(3)  # Let initial schema discovery settle first.
                    notice = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
                    self.respond(200, b"id:\ndata: " + json.dumps(notice).encode() + b"\n\n", sse=True)
                    return
                identifier, value = peer.pending[cursor]
                peer.resume_started.set()
                if value == "initialize":
                    result = {"jsonrpc": "2.0", "id": identifier,
                              "result": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}}}
                    self.respond(200, b"data: " + json.dumps(result).encode() + b"\n\n", sse=True)
                elif value == "auth":
                    self.respond(401, b"do-not-leak-token")
                elif value == "redirect":
                    self.respond(307, extra={"Location": "http://127.0.0.1:9/not-forwarded"})
                elif value == "expired":
                    self.respond(404)
                elif value == "unavailable":
                    self.respond(405)
                elif value == "exhaust":
                    self.respond(200, b": retry same stream\n\n", sse=True)
                elif value == "reset":
                    self.respond(200, b"id:\n\n", sse=True)
                else:
                    if value in {"cancel", "timeout"}:
                        peer.release.wait(3)
                    result = {"jsonrpc": "2.0", "id": identifier + (100 if value == "mismatch" else 0),
                              "result": {"content": [{"type": "text", "text": f"RESUMED-{value}"}]}}
                    self.respond(200, b"id: done\ndata: " + json.dumps(result).encode() + b"\n\n", sse=True)

            def do_DELETE(self) -> None:
                peer.deletes += 1
                self.respond(204)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/mcp"

    def __enter__(self) -> ResumePeer:
        self.thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
