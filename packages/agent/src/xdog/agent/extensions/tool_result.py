"""Typed trusted-extension tool results, validated at worker and host boundaries."""

from __future__ import annotations

import json
from typing import Any, cast

from xdog.agent.core import AgentToolResult
from xdog.agent.mcp.schema import json_value
from xdog.ai.types import ImageContent, TextContent
from xdog.ai.utils.image_files import decode_image

MAX_RESULT_BYTES = 3 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
_UNSET = object()


def details_snapshot(value: Any) -> Any:
    return json.loads(json_value(value, maximum=256 * 1024))


def normalize_result(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"content": [{"type": "text", "text": value}]}  # Existing text callbacks keep their wire behavior.
    if isinstance(value, AgentToolResult):
        content = []
        for part in value.content:
            if isinstance(part, TextContent):
                content.append({"type": "text", "text": part.text})
            elif isinstance(part, ImageContent):
                content.append({"type": "image", "data": part.data, "mimeType": part.mime_type})
            else:
                raise ValueError("Unsupported typed extension tool content; it was not discarded")
        if type(value.is_error) is not bool:
            raise ValueError("Invalid typed extension tool error flag")
        value = {"content": content, **({"details": value.details} if value.details is not None else {}),
                 **({"isError": True} if value.is_error else {})}
    if not isinstance(value, dict) or set(value) - {"content", "structuredContent", "details", "isError"}:
        raise ValueError("Extension tool result must be text or typed content/structuredContent/details/isError")
    content = value.get("content", [])
    if not isinstance(content, list) or len(content) > 64:
        raise ValueError("Extension tool content must contain at most 64 blocks")
    blocks = []
    images = 0
    for block in content:
        if not isinstance(block, dict):
            raise ValueError("Invalid extension tool content block")
        kind = block.get("type")
        if kind == "text" and set(block) == {"type", "text"} and isinstance(block.get("text"), str):
            if len(block["text"].encode()) > 1024 * 1024:
                raise ValueError("Extension rich text block exceeds 1 MiB")
            blocks.append(dict(block))
        elif kind == "image" and set(block) == {"type", "data", "mimeType"}:
            if not isinstance(block["data"], str) or not isinstance(block["mimeType"], str):
                raise ValueError("Invalid extension image data/MIME")
            decode_image(block["data"], block["mimeType"], max_bytes=MAX_IMAGE_BYTES)
            images += 1
            if images > 4:
                raise ValueError("Extension result contains more than four images")
            blocks.append(dict(block))
        else:
            raise ValueError("Unsupported/invalid extension tool block; it was not silently discarded")
    result: dict[str, Any] = {"content": blocks}
    if "isError" in value:
        if type(value["isError"]) is not bool:
            raise ValueError("Invalid extension tool error flag")
        result["isError"] = value["isError"]
    if "structuredContent" in value:
        result["structuredContent"] = details_snapshot(value["structuredContent"])
    if "details" in value:
        result["details"] = details_snapshot(value["details"])
    if not blocks and "structuredContent" not in result:
        raise ValueError("Extension rich result requires content or explicit structured data")
    try:
        encoded = json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode()) > MAX_RESULT_BYTES:
            raise ValueError("Extension rich result exceeds its frame budget")
    except (UnicodeError, RecursionError):
        raise ValueError("Invalid extension result encoding") from None
    return cast(dict[str, Any], json.loads(encoded))


def make_result(
    content: list[dict[str, Any]] | None = None, *, structured: Any = _UNSET, details: Any = _UNSET,
    is_error: bool = False
) -> dict[str, Any]:
    value: dict[str, Any] = {"content": content or []}
    if type(is_error) is not bool:
        raise ValueError("Invalid extension tool error flag")
    if is_error:
        value["isError"] = True
    if structured is not _UNSET:
        value["structuredContent"] = structured
    if details is not _UNSET:
        value["details"] = details
    return normalize_result(value)


def worker_result(value: Any) -> dict[str, Any]:
    result = normalize_result(value)
    if "details" in result:
        result["xdog_result_details"] = result.pop("details")
    if not isinstance(value, str):
        result["xdog_rich_result"] = 1
    return result


def host_result(value: dict[str, Any]) -> dict[str, Any]:
    if "xdog_rich_result" not in value:
        return value  # Original text-only callback protocol.
    if type(value["xdog_rich_result"]) is not int or value["xdog_rich_result"] != 1:
        raise ValueError("Unsupported extension result version")
    envelope = {"content": value.get("content", [])}
    if "isError" in value:
        envelope["isError"] = value["isError"]
    if "structuredContent" in value:
        envelope["structuredContent"] = value["structuredContent"]
    if "xdog_result_details" in value:
        envelope["details"] = value["xdog_result_details"]
    normalized = normalize_result(envelope)
    result = {**value, **normalized}
    if "details" in result:
        result["xdog_result_details"] = result.pop("details")
    return result
