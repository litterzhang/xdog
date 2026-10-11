"""Shared modern discovery and HTTP header mirrors; no remote schema fetching."""
from __future__ import annotations

import base64
import re
from typing import Any, cast

from xdog.agent.mcp.stdio import CURRENT_VERSION, McpError

TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def modern_discovery(response: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    supported = response.get("supportedVersions")
    if (not isinstance(supported, list) or not supported or len(supported) > 32
            or any(not isinstance(version, str) for version in supported) or CURRENT_VERSION not in supported):
        raise McpError("Server discovery did not advertise a supported modern version")
    capabilities = response.get("capabilities")
    metadata = response.get("_meta", {})
    if not isinstance(capabilities, dict) or not isinstance(metadata, dict):
        raise McpError("Invalid MCP discovery metadata")
    identity = metadata.get("io.modelcontextprotocol/serverInfo", {})
    if not isinstance(identity, dict):
        raise McpError("Invalid self-reported MCP server identity")
    return CURRENT_VERSION, capabilities, identity


def header_value(value: str | int | bool) -> str:
    text = "true" if value is True else "false" if value is False else str(value)
    safe = (text == text.strip() and all(char == "\t" or 0x20 <= ord(char) <= 0x7e for char in text)
            and not (text.startswith("=?base64?") and text.endswith("?=")))
    return text if safe else "=?base64?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="


def schema_headers(schema: dict[str, Any]) -> tuple[tuple[tuple[str, ...], str, str], ...]:
    """Reject annotations outside properties-only paths, including through refs/composition."""
    result = []
    names: set[str] = set()
    def visit(node: Any, path: tuple[str, ...], eligible: bool) -> None:
        if isinstance(node, dict):
            if "x-mcp-header" in node:
                name, kind = node["x-mcp-header"], node.get("type")
                if (not eligible or not path or not isinstance(name, str) or not TOKEN.fullmatch(name)
                        or not isinstance(kind, str) or kind not in {"string", "integer", "boolean"}
                        or name.lower() in names):
                    raise McpError("Invalid x-mcp-header annotation; tool rejected")
                names.add(name.lower())
                result.append((path, name, kind))
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    for prop, child in value.items():
                        visit(child, (*path, prop), eligible)
                elif isinstance(value, (dict, list)):
                    visit(value, path, False)
        elif isinstance(node, list):
            for child in node:
                visit(child, path, False)
    visit(schema, (), True)
    return tuple(result)


def argument_headers(paths: tuple[tuple[tuple[str, ...], str, str], ...], arguments: dict[str, Any]) -> dict[str, str]:
    result = {}
    for path, name, kind in paths:
        value: Any = arguments
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value is None:
            continue
        expected = {"string": str, "integer": int, "boolean": bool}[kind]
        if type(value) is not expected:
            raise McpError("MCP header parameter type/range does not match its schema")
        primitive = cast(str | int | bool, value)
        if kind == "integer" and not -(2**53 - 1) <= cast(int, primitive) <= 2**53 - 1:
            raise McpError("MCP header parameter integer exceeds the interoperable range")
        result["Mcp-Param-" + name] = header_value(primitive)
    return result
