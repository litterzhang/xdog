"""Strict extension context boundary: typed parts and complete tool/result batches."""
from __future__ import annotations

import base64
from typing import Any

from xdog.agent.core import AgentMessage
from xdog.agent.messages import dicts_to_messages, messages_to_dicts


def decode_context(value: Any, original: list[AgentMessage]) -> list[AgentMessage]:
    if not isinstance(value, list) or len(value) > 20000:
        raise ValueError("Invalid extension context message list")
    pending: dict[str, str] = {}
    for row in value:
        if not isinstance(row, dict) or row.get("role") not in {"user", "assistant", "toolResult", "custom"}:
            raise ValueError("Extension context cannot contain system/unknown roles")
        role = row["role"]
        if role == "custom":
            if not isinstance(row.get("type"), str) or not isinstance(row.get("data"), dict):
                raise ValueError("Invalid extension custom context message")
            continue
        parts = row.get("content")
        if not isinstance(parts, list):
            raise ValueError("Extension context requires typed content parts")
        if role == "toolResult":
            key, name = row.get("tool_call_id"), row.get("tool_name")
            if not isinstance(key, str) or key not in pending or pending[key] != name:
                raise ValueError("Extension context splits or invents a tool-result batch")
            pending.pop(key)
            if type(row.get("is_error", False)) is not bool:
                raise ValueError("Invalid tool-result error flag")
        elif pending:
            raise ValueError("Extension context splits a tool-result batch")
        for part in parts:
            if not isinstance(part, dict):
                raise ValueError("Invalid extension content part")
            kind = part.get("type")
            if kind == "text":
                if not isinstance(part.get("text"), str):
                    raise ValueError("Invalid extension text")
            elif kind == "image" and role in {"user", "toolResult"}:
                if not isinstance(part.get("data"), str) or not isinstance(part.get("mime_type"), str):
                    raise ValueError("Invalid extension image")
                base64.b64decode(part["data"], validate=True)
            elif kind == "thinking" and role == "assistant":
                if not isinstance(part.get("thinking"), str) or type(part.get("redacted", False)) is not bool:
                    raise ValueError("Invalid extension thinking block")
            elif kind == "toolCall" and role == "assistant":
                key, name = part.get("id"), part.get("name")
                if (not isinstance(key, str) or not key or key in pending or not isinstance(name, str)
                        or not isinstance(part.get("arguments"), dict)):
                    raise ValueError("Invalid extension tool call")
                pending[key] = name
            else:
                raise ValueError("Unsupported extension context part; no silent dropping")
            for signature in ("text_signature", "thinking_signature", "thought_signature"):
                if signature in part and not isinstance(part[signature], str):
                    raise ValueError("Invalid extension content signature")
    if pending:
        raise ValueError("Extension context leaves an incomplete tool-result batch")
    if value == messages_to_dicts(original):
        return original  # Preserve every original typed field on a no-op hook.
    return dicts_to_messages(value)
