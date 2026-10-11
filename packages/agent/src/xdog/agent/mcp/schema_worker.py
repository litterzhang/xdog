"""Private local schema service. No model/tool loop, URL retrieval or project imports."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import OrderedDict
from typing import Any

from xdog.agent.mcp.schema import CompiledSchema, compile_schema, json_value
from xdog.agent.mcp.stdio import McpError

MAX_FRAME = 2 * 1024 * 1024
MAX_CACHE = 4 * 1024 * 1024


def main() -> None:
    cache: OrderedDict[str, tuple[CompiledSchema, int]] = OrderedDict()
    retained = 0
    while raw := sys.stdin.buffer.readline(MAX_FRAME + 1):
        if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
            return
        response: dict[str, Any] = {"ok": False, "error": "Invalid local schema operation"}
        try:
            request = json.loads(raw)
            if (
                not isinstance(request, dict)
                or type(request.get("id")) is not int
                or request.get("action") not in ("compile", "validate")
                or not isinstance(request.get("key"), str)
                or type(request.get("input")) is not bool
                or type(request.get("legacy")) is not bool
            ):
                raise McpError("Invalid local schema operation")
            response["id"] = request["id"]
            key = request["key"]
            encoded = json_value(request["schema"], maximum=256 * 1024)
            expected = hashlib.sha256(
                json_value([request["schema"], request["input"], request["legacy"]]).encode()
            ).hexdigest()
            if key != expected:
                raise McpError("MCP schema document changed after compilation")
            if key in cache:
                compiled, size = cache.pop(key)
                cache[key] = compiled, size
            else:
                compiled = compile_schema(request["schema"], input_schema=request["input"], legacy=request["legacy"])
                size = len(encoded.encode())
                cache[key] = compiled, size
                retained += size
                while len(cache) > 32 or retained > MAX_CACHE:
                    _key, (_schema, removed) = cache.popitem(last=False)
                    retained -= removed
            if request["action"] == "validate":
                compiled.validate(request["value"], label="value")
                response = {"id": request["id"], "ok": True}
            else:
                response = {
                    "id": request["id"],
                    "ok": True,
                    "schema": compiled.schema,
                    "model": compiled.model_parameters,
                }
        except McpError as exc:
            response["error"] = str(exc)
        except Exception:
            response["error"] = "Local schema validation failed"
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
