"""Use the SDK transport, closing only this connection's resources."""

from __future__ import annotations

import asyncio
import json
from typing import Any

try:
    import acp
except ImportError as exc:
    raise ImportError("ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc
from xdog.agent.acp.adapter import AcpAdapter
from xdog.agent.acp.contracts import MAX_INPUT_BYTES, finite_json

_RESERVED_META = frozenset(
    {
        "session_id",
        "sessionId",
        "cwd",
        "prompt",
        "protocol_version",
        "protocolVersion",
        "client_capabilities",
        "clientCapabilities",
        "client_info",
        "clientInfo",
        "mcp_servers",
        "mcpServers",
        "additional_directories",
        "additionalDirectories",
        "mode_id",
        "modeId",
        "config_id",
        "configId",
        "value",
        "method_id",
        "methodId",
        "field_meta",
        "_meta",
    }
)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate ACP JSON field")
        result[key] = value
    return result


class ValidatedReader(asyncio.StreamReader):
    """SDK metadata is flattened to kwargs; prevent it shadowing validated fields."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        super().__init__(limit=MAX_INPUT_BYTES)
        self.reader = reader

    async def readuntil(self, separator: Any = b"\n") -> bytes:
        try:
            line = await self.reader.readuntil(separator)
        except asyncio.LimitOverrunError as exc:
            raise ValueError("ACP frame exceeds capacity") from exc
        except asyncio.IncompleteReadError as exc:
            if exc.partial:
                self._validate(exc.partial)
            raise
        self._validate(line)
        return line

    @staticmethod
    def _validate(line: bytes) -> None:
        if not line.strip():
            return
        if len(line) > MAX_INPUT_BYTES:
            raise ValueError("ACP frame exceeds capacity")
        value = json.loads(line, object_pairs_hook=_unique)
        finite_json(value)
        if isinstance(value, dict) and isinstance(value.get("params"), dict):
            meta = value["params"].get("_meta", {})
            if not isinstance(meta, dict) or _RESERVED_META.intersection(meta):
                raise ValueError("ACP metadata cannot override protocol routing fields")


async def serve(
    adapter: AcpAdapter, reader: asyncio.StreamReader | None = None, writer: asyncio.StreamWriter | None = None
) -> None:
    if reader is None or writer is None:
        reader, writer = await acp.stdio.stdio_streams(limit=MAX_INPUT_BYTES)
    guarded = ValidatedReader(reader)
    try:
        await acp.run_agent(adapter, writer, guarded, stdio_buffer_limit_bytes=MAX_INPUT_BYTES)
    finally:
        await adapter.aclose()
