"""Owned bounded IPC chunks for MCP previews/catalogs, not oversized UI frames."""
from __future__ import annotations

import base64
import hashlib
import json
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, BinaryIO

from xdog.agent.mcp.completion import McpCompletion, decode_payload
from xdog.agent.mcp.context import McpBlock, McpDocument, McpEntry
from xdog.claw.channels.tui.source_reader import SourceReceiver


def _decode(file: BinaryIO) -> tuple[McpEntry, ...] | McpDocument | McpCompletion:
    value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError("Invalid MCP data envelope")
    if value.get("kind") == "completion":
        return decode_payload(value.get("completion"))
    if value.get("kind") == "catalog":
        if not isinstance(value.get("entries"), list) or len(value["entries"]) > 512:
            raise ValueError("Invalid MCP catalog payload")
        result = []
        for row in value["entries"]:
            if (not isinstance(row, dict) or any(not isinstance(row.get(key), str)
                                                for key in ("server", "kind", "name", "title", "description"))):
                raise ValueError("Invalid MCP catalog entry")
            args = tuple(tuple(arg) for arg in row.get("arguments", ()))
            if any(len(arg) != 3 or not isinstance(arg[0], str) or not isinstance(arg[1], str)
                   or type(arg[2]) is not bool for arg in args):
                raise ValueError("Invalid MCP argument metadata")
            result.append(McpEntry(row["server"], row["kind"], row["name"], row["title"], row["description"], args))
        return tuple(result)
    if value.get("kind") != "document":
        raise ValueError("Unknown MCP payload")
    document = value.get("document")
    if (not isinstance(document, dict) or any(not isinstance(document.get(key), str)
                                            for key in ("server", "kind", "name"))
            or not isinstance(document.get("blocks"), list) or len(document["blocks"]) > 128):
        raise ValueError("Invalid MCP document")
    blocks = []
    for block in document["blocks"]:
        if not isinstance(block, dict) or any(not isinstance(block.get(key), str)
                                              for key in ("role", "text", "mime_type", "data", "uri")):
            raise ValueError("Invalid MCP block")
        base64.b64decode(block["data"], validate=True)
        blocks.append(McpBlock(**block))
    return McpDocument(document["server"], document["kind"], document["name"], tuple(blocks))


class McpDataReceiver:
    def __init__(self, publish: Callable[[dict[str, Any]], None]) -> None:
        self._receiver = SourceReceiver(lambda event: publish({**event, "type": {
            "source_ready": "mcp_data_ready", "source_failed": "mcp_data_failed",
            "source_loading": "mcp_data_loading", "source_progress": "mcp_data_progress",
        }[event["type"]]}), decode=_decode)

    def expect(self, identifier: str, group: str, session: str) -> None:
        self._receiver.expect(identifier, group, session)

    def feed(self, event: dict[str, Any]) -> None:
        self._receiver.feed({**event, "type": event["type"].replace("mcp_data_", "source_", 1)})

    def close(self) -> None:
        self._receiver.close()


def spool_mcp_data(value: tuple[McpEntry, ...] | McpDocument | McpCompletion, *,
                   content_types: bool = True) -> tuple[BinaryIO, int, str]:
    file = tempfile.TemporaryFile()
    try:
        payload: dict[str, Any] = ({"kind": "catalog", "entries": [asdict(entry) for entry in value]}
                   if isinstance(value, tuple) else {"kind": "completion", "completion": asdict(value)}
                   if isinstance(value, McpCompletion) else {"kind": "document", "document": asdict(value)})
        if isinstance(value, McpDocument) and not content_types:
            if any(block.is_binary and not block.data for block in value.blocks):
                raise ValueError("Empty binary MCP content requires an updated typed-content viewer; "
                                 "no draft downgrade")
            for block in payload["document"]["blocks"]:
                block.pop("content_type", None)  # Existing nonempty binary/text viewers remain wire-compatible.
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        file.write(data)
        file.seek(0)
        return file, len(data), hashlib.sha256(data).hexdigest()
    except BaseException:
        file.close()
        raise
