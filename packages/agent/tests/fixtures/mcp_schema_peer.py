"""Independent MCP peer for dialect/structured data, usable over stdio or HTTP."""

import json
import sys
from pathlib import Path

INPUT = {
    "$id": "https://schemas.invalid/input",
    "type": "object",
    "properties": {"value": {"$ref": "nullable"}, "forbidden": False},
    "required": ["value"],
    "$defs": {"nullable": {"$id": "nullable", "type": ["string", "null"]}},
    "additionalProperties": False,
}
ARRAY = {"type": "array", "items": {"type": "object", "required": ["tag"], "properties": {"tag": {"type": "string"}}}}


def response(request, *, legacy=False, choice="array", override=None):
    method, params = request["method"], request.get("params", {})
    if method == "server/discover":
        if legacy:
            return {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Method not found"}}
        result = {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}}}
    elif method == "initialize":
        result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}}
    elif method == "tools/list":
        schemas = {
            "array": ARRAY,
            "content": ARRAY,
            "bad-content": ARRAY,
            "null": {"type": "null"},
            "scalar": {"enum": [False, 0, ""]},
            "any": True,
            "reject": False,
            "missing": {"type": "null"},
            "object": {"type": "object"},
            "nan": {"type": "number"},
            "error": {"type": "object", "required": ["ok"]},
        }
        output = schemas[choice] if not legacy else {"type": "object"}
        result = {"tools": [{"name": "rich", "inputSchema": INPUT, "outputSchema": output}]}
    elif method == "tools/call":
        assert params["name"] == "rich" and params["arguments"] == {"value": None}
        values = {
            "array": [{"tag": "STRUCTURED-ARRAY-TAIL-987"}],
            "content": [{"tag": "STRUCTURED-ARRAY-TAIL-987"}],
            "bad-content": [{"tag": "STRUCTURED-ARRAY-TAIL-987"}],
            "null": None,
            "scalar": False,
            "any": [False, None, 0, ""],
            "reject": "not valid",
            "object": {"tag": "STRUCTURED-OBJECT-TAIL"},
            "nan": float("nan"),
        }
        value = values.get(choice)
        result = {"content": [{"type": "text", "text": "human summary without full data"}]}
        if choice != "missing":
            result["structuredContent"] = value if not legacy else {"tag": "STRUCTURED-OBJECT-TAIL"}
        if choice == "error":
            result.update(isError=True, structuredContent={"diagnostic": "ERROR-STRUCTURED-TAIL", "retry": False})
            result["content"] = [{"type": "text", "text": "RICH-MCP-ERROR"}, {"type": "image", "mimeType": "image/png",
                "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII="}]
        if choice in ("content", "bad-content"):
            result["content"] = [
                {"type": "text", "text": "MCP-CONTENT-TEXT", "annotations": {"audience": ["assistant"],
                    "priority": 0.25, "lastModified": "2024-01-01T00:00:00Z"},
                    "_meta": {"fixture/provenance": "MCP-CONTENT-METADATA"}},
                {"type": "image", "mimeType": "image/png",
                    "data": ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5io"
                             "AAAAASUVORK5CYII="),
                    "_meta": {"fixture/image": "MCP-IMAGE-METADATA"}},
                {"type": "audio", "data": "UklGRg==" if choice == "content" else "bad!",
                    "mimeType": "audio/wav", "_meta": {"fixture/audio": "MCP-CONTENT-AUDIO"}},
                {"type": "resource", "resource": {"uri": "test://embedded", "blob": "AAECAw==",
                    "mimeType": "application/octet-stream", "_meta": {"fixture/inner": "retained"}},
                    "annotations": {"audience": ["user"]}},
                {"type": "resource_link", "name": "document", "uri": "https://must-not-fetch.invalid/document",
                    "_meta": {"fixture/link": "MCP-CONTENT-LINK"}},
            ]
        if override is not None:
            result.update(override)
    else:
        raise AssertionError("Unexpected MCP method " + method)
    return {"jsonrpc": "2.0", "id": request["id"], "result": result}


def main():
    profile, log = sys.argv[1:]
    legacy = profile.startswith("legacy")
    choice = "object" if profile == "legacy" else profile.removeprefix("modern-").removeprefix("legacy-")
    for line in sys.stdin:
        request = json.loads(line)
        with Path(log).open("a") as stream:
            stream.write(json.dumps(request) + "\n")
        if "id" not in request:
            continue
        print(json.dumps(response(request, legacy=legacy, choice=choice), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
