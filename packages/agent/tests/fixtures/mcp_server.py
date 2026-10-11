"""Offline protocol peer; intentionally no SDK/client implementation reuse."""
import json
import sys
import threading
import time
from pathlib import Path

MODE, LOG = sys.argv[1:]
lock = threading.Lock()

def write(value):
    with lock:
        print(json.dumps({"jsonrpc": "2.0", **value}), flush=True)

def answer(identifier, result):
    write({"id": identifier, "result": result})

for raw in sys.stdin:
    request = json.loads(raw)
    with Path(LOG).open("a") as log:
        log.write(json.dumps(request) + "\n")
    method, identifier = request["method"], request.get("id")
    if identifier is None:
        continue
    if method == "server/discover":
        if MODE == "legacy":
            write({"id": identifier, "error": {"code": -32601, "message": "Method not found"}})
        elif MODE == "reject":
            write({"id": identifier, "error": {"code": -32022, "message": "Unsupported version"}})
        else:
            answer(identifier, {"resultType": "complete", "supportedVersions": ["2026-07-28"],
                                "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "fixture", "version": "1"}},
                                "capabilities": ({"resources": {}} if MODE == "context-only" else
                                                 {"prompts": {}} if MODE == "prompts-only" else
                                                 {"tools": {}, "resources": {}, "prompts": {}, "completions": {}})})
    elif method == "initialize":
        answer(identifier, {"protocolVersion": "2025-11-25", "serverInfo": {"name": "fixture", "version": "1"},
                            "capabilities": {"tools": {"listChanged": True}}})
    elif method == "resources/list":
        if MODE == "empty-content":
            answer(identifier, {"resources": [{"name": "empty binary", "uri": "fixture://empty",
                                                "mimeType": "application/octet-stream"}]})
        elif request["params"].get("cursor"):
            answer(identifier, {"resources": [{"name": "binary", "uri": "fixture://binary",
                                                "mimeType": "application/octet-stream"}]})
        else:
            answer(identifier, {"resources": [{"name": "text", "uri": "fixture://text", "title": "Fixture document"}],
                                "nextCursor": "resource2"})
    elif method == "resources/templates/list":
        answer(identifier, {"resourceTemplates": [{"name": "template", "uriTemplate": "fixture://document/{name}"}]})
    elif method == "resources/read":
        uri = request["params"]["uri"]
        payload = ({"uri": uri, "mimeType": "application/octet-stream", "blob": "AAECAw=="}
                   if uri == "fixture://binary" else {"uri": uri, "text": "UNTRUSTED-RESOURCE-START\n"
                                                       + "x" * 200000 + "RESOURCE-TAIL"})
        if MODE == "empty-content":
            payload = {"uri": uri, "mimeType": "application/octet-stream", "blob": "",
                       "_meta": {"fixture/empty": "EMPTY-BINARY-METADATA"}}
        answer(identifier, {"contents": [payload]})
    elif method == "prompts/list":
        answer(identifier, {"prompts": [{"name": "review", "description": "fixture review", "arguments": [
            {"name": "topic", "required": True, "description": "Review topic"}]}]})
    elif method == "completion/complete":
        reference, argument = request["params"]["ref"], request["params"]["argument"]
        if reference == {"type": "ref/resource", "uri": "fixture://text"}:
            assert argument["name"] == "opaque-field"
            answer(identifier, {"completion": {"values": ["OPAQUE-ARGUMENT-VALUE", "not-a-uri"],
                                                "total": 2, "hasMore": False}})
        else:
            assert reference in ({"type": "ref/prompt", "name": "review"},
                                 {"type": "ref/resource", "uri": "fixture://document/{name}"})
            assert argument["name"] == ("topic" if reference["type"] == "ref/prompt" else "name")
            answer(identifier, {"completion": {"values": ["completed value", "alternative"],
                                                "total": 5, "hasMore": True}})
    elif method == "prompts/get":
        text = "PROMPT-" + request["params"]["arguments"]["topic"]
        answer(identifier, {"messages": [
            {"role": "user", "content": {"type": "text", "text": text}},
            {"role": "assistant", "content": {"type": "text", "text": "quoted assistant example"}}]})
    elif method == "tools/list":
        if request["params"].get("cursor"):
            answer(identifier, {"tools": [{"name": "structured", "inputSchema": {"type": "object"},
                 "outputSchema": {"type": "object", "required": ["count"],
                                  "properties": {"count": {"type": "integer"}}}}]})
        else:
            schema = {"type": "object", "required": ["value"], "properties": {"value": {"type": "string"}},
                      "unevaluatedProperties": False}
            if MODE == "external-schema":
                schema["$ref"] = "https://not-fetched.invalid/schema"
            answer(identifier, {"tools": [{"name": "echo.remote", "description": "fixture echo",
                                           "inputSchema": schema, "annotations": {"readOnlyHint": True}}],
                                "nextCursor": "page2"})
    elif method == "tools/call":
        arguments = request["params"]["arguments"]
        value = arguments.get("value", "")
        if value == "eof":
            break
        if value == "malformed":
            print("not-json", flush=True)
            continue
        if value == "delay":
            def later(id=identifier):
                time.sleep(0.6)
                answer(id, {"content": [{"type": "text", "text": "late response"}]})
            threading.Thread(target=later, daemon=True).start()
            continue
        if request["params"]["name"] == "structured":
            answer(identifier, {"structuredContent": {"count": arguments.get("count", 2)}, "content": []})
        elif value == "error":
            answer(identifier, {"isError": True, "content": [{"type": "text", "text": "fixture error"}]})
        else:
            answer(identifier, {"content": [{"type": "text", "text": value}]})
            if MODE == "legacy":
                write({"method": "notifications/tools/list_changed", "params": {}})
    else:
        write({"id": identifier, "error": {"code": -32601, "message": "unknown"}})
