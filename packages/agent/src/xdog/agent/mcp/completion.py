"""Bounded inert completion values and catalog-template argument metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from xdog.agent.mcp.stdio import McpError

_VARIABLE = re.compile(
    r"((?:[A-Za-z0-9_]|%[0-9A-Fa-f]{2})+(?:\.(?:[A-Za-z0-9_]|%[0-9A-Fa-f]{2})+)*)"
    r"(?:\*|:[1-9][0-9]{0,3})?"
)


def bounded(value: Any, maximum: int, *, empty: bool = True) -> str:
    if not isinstance(value, str) or not empty and not value:
        raise McpError("Invalid MCP completion string")
    try:
        if len(value.encode("utf-8")) > maximum:
            raise McpError("MCP completion string exceeds its limit")
    except UnicodeError:
        raise McpError("Invalid MCP completion string encoding") from None
    return value


def arguments(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 128:
        raise McpError("Invalid/excessive MCP completion context")
    result = {bounded(key, 256, empty=False): bounded(item, 8192) for key, item in value.items()}
    if sum(len(key.encode()) + len(item.encode()) for key, item in result.items()) > 65536:
        raise McpError("MCP completion context exceeds its limit")
    return result


def template_arguments(template: str) -> tuple[str, ...]:
    bounded(template, 8192, empty=False)
    names: dict[str, None] = {}
    for expression in re.finditer(r"\{([^{}]+)\}|[{}]", template):
        text = expression[1]
        if text is None:
            raise McpError("Invalid MCP URI template")
        if text[0] in "+#./;?&":
            text = text[1:]
        for variable in text.split(","):
            match = _VARIABLE.fullmatch(variable)
            if match is None:
                raise McpError("Invalid MCP URI template variable")
            names[bounded(match[1], 256)] = None
            if len(names) > 128:
                raise McpError("Excessive MCP URI template variables")
    return tuple(names)


def values(reply: Any) -> tuple[tuple[str, ...], int | None, bool]:
    if not isinstance(reply, dict) or reply.get("resultType", "complete") != "complete":
        raise McpError("Unsupported/deferred MCP completion result")
    result = reply.get("completion")
    if not isinstance(result, dict) or not isinstance(result.get("values"), list) or len(result["values"]) > 100:
        raise McpError("Invalid/excessive MCP completion values")
    chosen = tuple(bounded(value, 8192) for value in result["values"])
    total, more = result.get("total"), result.get("hasMore", False)
    if (
        total is not None
        and (type(total) is not int or not len(chosen) <= total <= 2**53 - 1)
        or type(more) is not bool
    ):
        raise McpError("Invalid MCP completion count/continuation")
    return chosen, total, more


@dataclass(frozen=True, slots=True)
class McpCompletion:
    server: str
    kind: str
    name: str
    argument: str
    value: str
    context: tuple[tuple[str, str], ...]
    values: tuple[str, ...]
    total: int | None = None
    has_more: bool = False


def decode_payload(value: Any) -> McpCompletion:
    if not isinstance(value, dict) or value.get("kind") not in ("prompt", "template", "resource"):
        raise McpError("Invalid MCP completion payload")
    pairs = value.get("context")
    if (
        not isinstance(pairs, list)
        or len(pairs) > 128
        or any(
            not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[0], str) or not isinstance(pair[1], str)
            for pair in pairs
        )
    ):
        raise McpError("Invalid MCP completion context payload")
    context = arguments(dict(pairs))
    if len(context) != len(pairs):
        raise McpError("Duplicate MCP completion context key")
    chosen, total, more = values(
        {"completion": {"values": value.get("values"), "total": value.get("total"), "hasMore": value.get("has_more")}}
    )
    return McpCompletion(
        bounded(value.get("server"), 256, empty=False),
        value["kind"],
        bounded(value.get("name"), 8192, empty=False),
        bounded(value.get("argument"), 256, empty=False),
        bounded(value.get("value"), 8192),
        tuple(context.items()),
        chosen,
        total,
        more,
    )


def request_arguments(value: Any) -> tuple[str, str, dict[str, str]]:
    import json

    if not isinstance(value, dict) or set(value) != {"argument", "value", "context"}:
        raise McpError("Invalid MCP completion request fields")
    encoded = bounded(value["context"], 131072)
    try:
        context = json.loads(encoded)
    except (ValueError, RecursionError):
        raise McpError("Invalid MCP completion context encoding") from None
    return bounded(value["argument"], 256, empty=False), bounded(value["value"], 8192), arguments(context)
