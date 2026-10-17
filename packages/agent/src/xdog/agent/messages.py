"""Lossless JSON round-trip for agent messages.

The fidelity is the point, and it is easy to lose. An assistant turn carries
thinking blocks whose ``thinking_signature`` is the continuity token for
extended reasoning; a user turn may carry images; a tool result may have several
parts. Flatten any of those to a string — as one earlier implementation did —
and a restored session is quietly a different conversation from the one that was
saved.
"""

from __future__ import annotations

import json
import math
from typing import Any

from xdog.agent.core import AgentMessage, CustomAgentMessage
from xdog.ai.types import (
    AssistantMessage,
    CostBreakdown,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultContentPart,
    ToolResultMessage,
    Usage,
    UserContentPart,
    UserMessage,
)

# ---------------------------------------------------------------------------
# Serialize agent messages -> dicts (for JSON persistence)
# ---------------------------------------------------------------------------

def _serialize_user_content_part(part: UserContentPart) -> dict[str, Any]:
    if isinstance(part, TextContent):
        return {"type": "text", "text": part.text}
    if isinstance(part, ImageContent):
        return {"type": "image", "data": part.data, "mime_type": part.mime_type}
    return {"type": "text", "text": str(part)}


def _serialize_assistant_content_part(part: Any) -> dict[str, Any]:
    if isinstance(part, TextContent):
        d: dict[str, Any] = {"type": "text", "text": part.text}
        if part.text_signature:
            d["text_signature"] = part.text_signature
        return d
    if isinstance(part, ThinkingContent):
        d = {"type": "thinking", "thinking": part.thinking}
        if part.thinking_signature:
            d["thinking_signature"] = part.thinking_signature
        if part.redacted:
            d["redacted"] = True
        return d
    if isinstance(part, ToolCall):
        d = {"type": "toolCall", "id": part.id, "name": part.name, "arguments": part.arguments}
        if part.thought_signature:
            d["thought_signature"] = part.thought_signature
        return d
    return {"type": "text", "text": str(part)}


def _serialize_tool_result_content_part(part: ToolResultContentPart) -> dict[str, Any]:
    if isinstance(part, TextContent):
        return {"type": "text", "text": part.text}
    if isinstance(part, ImageContent):
        return {"type": "image", "data": part.data, "mime_type": part.mime_type}
    return {"type": "text", "text": str(part)}


def _json_details(value: Any) -> Any:
    """Keep bounded JSON metadata losslessly; live/non-JSON handles remain transient."""
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 64 or count > 50000:
            return None
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                return None
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (str, int, float, bool):
            return None
        elif type(item) is float and not math.isfinite(item):
            return None
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return json.loads(encoded) if len(encoded.encode()) <= 2 * 1024 * 1024 else None
    except (ValueError, UnicodeError, RecursionError):
        return None


def message_to_dict(msg: AgentMessage) -> dict[str, Any]:
    """Serialize a single ai.types.Message to a plain dict."""
    if isinstance(msg, UserMessage):
        if isinstance(msg.content, str):
            content = [{"type": "text", "text": msg.content}]
        else:
            content = [_serialize_user_content_part(p) for p in msg.content]
        return {"role": "user", "content": content}

    if isinstance(msg, AssistantMessage):
        content = [_serialize_assistant_content_part(p) for p in msg.content]
        d: dict[str, Any] = {
            "role": "assistant",
            "content": content,
            "usage": {
                "input": msg.usage.input,
                "output": msg.usage.output,
                "cache_read": msg.usage.cache_read,
                "cache_write": msg.usage.cache_write,
                "total_tokens": msg.usage.total_tokens,
                "cost": {
                    "input": msg.usage.cost.input,
                    "output": msg.usage.cost.output,
                    "cache_read": msg.usage.cost.cache_read,
                    "cache_write": msg.usage.cost.cache_write,
                    "total": msg.usage.cost.total,
                },
            },
        }
        if msg.model:
            d["model"] = msg.model
        if msg.provider:
            d["provider"] = msg.provider
        if msg.api:
            d["api"] = msg.api
        if msg.response_id:
            d["response_id"] = msg.response_id
        if msg.stop_reason != "stop":
            d["stop_reason"] = msg.stop_reason
        if msg.error_message:
            d["error_message"] = msg.error_message
        if msg.timestamp:
            d["timestamp"] = msg.timestamp
        return d

    if isinstance(msg, ToolResultMessage):
        content = [_serialize_tool_result_content_part(p) for p in msg.content]
        result = {
            "role": "toolResult",
            "tool_call_id": msg.tool_call_id,
            "tool_name": msg.tool_name,
            "content": content,
            "is_error": msg.is_error,
        }
        details = _json_details(msg.details)
        if details is not None:
            result["details"] = details
        return result

    if isinstance(msg, CustomAgentMessage):
        return {"role": "custom", "type": msg.type, "data": msg.data}

    return {"role": "unknown", "content": str(msg)}


def messages_to_dicts(messages: list[AgentMessage]) -> list[dict[str, Any]]:
    """Serialize a list of agent messages to plain dicts."""
    return [message_to_dict(m) for m in messages]


# ---------------------------------------------------------------------------
# Deserialize dicts -> agent messages
# ---------------------------------------------------------------------------

def _parse_user_content(parts: list[dict[str, Any]]) -> tuple[UserContentPart, ...]:
    result: list[UserContentPart] = []
    for p in parts:
        ptype = p.get("type", "text")
        if ptype == "text":
            result.append(TextContent(text=p.get("text", "")))
        elif ptype == "image":
            result.append(ImageContent(data=p.get("data", ""), mime_type=p.get("mime_type", "image/png")))
    return tuple(result)


def _parse_assistant_content(parts: list[dict[str, Any]]) -> tuple[Any, ...]:
    from xdog.ai.types import AssistantContentPart

    result: list[AssistantContentPart] = []
    for p in parts:
        ptype = p.get("type", "text")
        if ptype == "text":
            result.append(TextContent(
                text=p.get("text", ""),
                text_signature=p.get("text_signature"),
            ))
        elif ptype == "thinking":
            result.append(ThinkingContent(
                thinking=p.get("thinking", ""),
                thinking_signature=p.get("thinking_signature"),
                redacted=p.get("redacted", False),
            ))
        elif ptype == "toolCall":
            result.append(ToolCall(
                id=p.get("id", ""),
                name=p.get("name", ""),
                arguments=p.get("arguments", {}),
                thought_signature=p.get("thought_signature"),
            ))
    return tuple(result)


def _parse_tool_result_content(parts: list[dict[str, Any]]) -> tuple[ToolResultContentPart, ...]:
    result: list[ToolResultContentPart] = []
    for p in parts:
        ptype = p.get("type", "text")
        if ptype == "text":
            result.append(TextContent(text=p.get("text", "")))
        elif ptype == "image":
            result.append(ImageContent(data=p.get("data", ""), mime_type=p.get("mime_type", "image/png")))
    return tuple(result)


def dict_to_message(d: dict[str, Any]) -> AgentMessage:
    """Deserialize a plain dict back into an ai.types.Message."""
    role = d.get("role", "user")

    if role == "user":
        content_raw = d.get("content", [])
        if isinstance(content_raw, str):
            return UserMessage(content=content_raw)
        return UserMessage(content=_parse_user_content(content_raw))

    if role == "assistant":
        content_raw = d.get("content", [])
        usage_raw = d.get("usage", {})
        if not isinstance(usage_raw, dict):
            usage_raw = {}
        cost_raw = usage_raw.get("cost", {})
        if not isinstance(cost_raw, dict):
            cost_raw = {}
        usage = Usage(
            input=usage_raw.get("input", 0),
            output=usage_raw.get("output", 0),
            cache_read=usage_raw.get("cache_read", 0),
            cache_write=usage_raw.get("cache_write", 0),
            total_tokens=usage_raw.get("total_tokens", 0),
            cost=CostBreakdown(
                input=cost_raw.get("input", 0.0),
                output=cost_raw.get("output", 0.0),
                cache_read=cost_raw.get("cache_read", 0.0),
                cache_write=cost_raw.get("cache_write", 0.0),
                total=cost_raw.get("total", 0.0),
            ),
        )
        return AssistantMessage(
            content=_parse_assistant_content(content_raw),
            api=d.get("api", ""),
            model=d.get("model", ""),
            provider=d.get("provider", ""),
            response_id=d.get("response_id"),
            usage=usage,
            stop_reason=d.get("stop_reason", "stop"),
            error_message=d.get("error_message"),
            timestamp=d.get("timestamp", 0.0),
        )

    if role == "toolResult":
        content_raw = d.get("content", [])
        return ToolResultMessage(
            tool_call_id=d.get("tool_call_id", ""),
            tool_name=d.get("tool_name", ""),
            content=_parse_tool_result_content(content_raw),
            details=_json_details(d.get("details")),
            is_error=d.get("is_error", False),
        )

    if role == "custom":
        return CustomAgentMessage(type=d.get("type", "custom"), data=d.get("data", {}))

    return UserMessage(content=str(d))


def dicts_to_messages(dicts: list[dict[str, Any]]) -> list[AgentMessage]:
    """Deserialize plain dicts back into agent messages."""
    return [dict_to_message(d) for d in dicts]


def default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Project named canonical context content; keep unknown custom metadata out of the LLM."""
    from xdog.agent.extensions.context_message import llm_message
    from xdog.agent.external_event import llm_message as external_message
    from xdog.agent.session_input import llm_message as session_message

    result: list[Message] = []
    for message in messages:
        if isinstance(message, (UserMessage, AssistantMessage, ToolResultMessage)):
            result.append(message)
        elif (projected := llm_message(message)) is not None:
            result.append(projected)
        elif (external := external_message(message)) is not None:
            result.append(external)
        elif (session_input := session_message(message)) is not None:
            result.append(session_input)
    return result
