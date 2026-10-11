"""Independent JSON-RPC peer shared by owned stdio and loopback HTTP tests."""

import json
import sys
import threading
import time
from pathlib import Path

TEMPLATE = "fixture://document/{name}{?format}"


def response(request, *, legacy=False, capabilities=True, bad=None, completion_only=False, invalid_completion=False):
    method, params = request["method"], request.get("params", {})
    caps = {} if completion_only else {"prompts": {}, "resources": {}}
    if capabilities:
        caps["completions"] = True if invalid_completion else {}
    if method == "server/discover":
        if legacy:
            return {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Method not found"}}
        result = {"supportedVersions": ["2026-07-28"], "capabilities": caps}
    elif method == "initialize":
        result = {"protocolVersion": "2025-11-25", "capabilities": caps}
    elif method == "prompts/list":
        result = {
            "prompts": [{"name": "review", "arguments": [{"name": "topic", "required": True}, {"name": "language"}]}]
        }
    elif method == "resources/templates/list":
        result = {"resourceTemplates": [{"name": "document", "uriTemplate": TEMPLATE}]}
    elif method == "completion/complete":
        reference = params["ref"]
        assert (reference == {"type": "ref/prompt", "name": "review"}
                or reference["type"] == "ref/resource" and isinstance(reference.get("uri"), str))
        assert params["argument"]["name"] in ("topic", "language", "name", "format", "opaque-field")
        assert all(isinstance(value, str) for value in params.get("context", {}).get("arguments", {}).values())
        result = {"completion": {"values": ["completed value", "第二项"], "total": 4, "hasMore": True}}
        if bad is not None:
            result = bad
    elif method == "prompts/get":
        result = {
            "messages": [
                {"role": "user", "content": {"type": "text", "text": "PREVIEW-" + params["arguments"]["topic"]}}
            ]
        }
    elif method == "resources/read":
        result = {"contents": [{"uri": params["uri"], "text": "RESOURCE-PREVIEW"}]}
    else:
        raise AssertionError("Unexpected request; completion must not invoke a tool or open subscriptions: " + method)
    return {"jsonrpc": "2.0", "id": request["id"], "result": result}


def main():
    mode, filename = sys.argv[1:]
    lock = threading.Lock()

    def write(value):
        with lock:
            print(json.dumps(value, ensure_ascii=False), flush=True)

    for line in sys.stdin:
        request = json.loads(line)
        with Path(filename).open("a") as stream:
            stream.write(json.dumps(request) + "\n")
        if "id" not in request:
            continue
        value = response(request, legacy=mode == "legacy", capabilities=mode != "no-completions",
                         completion_only=mode in ("completion-only", "invalid-completion-capability"),
                         invalid_completion=mode == "invalid-completion-capability")
        slow_catalog = mode == "slow-completions" and request["method"] in ("prompts/list", "completion/complete")
        delayed_value = request["method"] == "completion/complete" and request["params"]["argument"]["value"] == "delay"
        if slow_catalog or delayed_value:

            def later(reply=value, delay=0.3 if slow_catalog else 0.6):
                time.sleep(delay)
                write(reply)

            threading.Thread(target=later, daemon=True).start()
        else:
            write(value)


if __name__ == "__main__":
    main()
