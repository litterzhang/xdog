"""Inbound Anthropic Messages validation and best-effort projection."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, cast

from xdog.ai.native import NativeOperation, ProtocolRequest
from xdog.ai.proxy_anthropic_diagnostics import projection_issues
from xdog.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    JsonSchemaFormat,
    StreamOptions,
    SystemPromptBlock,
    TextContent,
    ThinkingContent,
    ThinkingLevel,
    Tool,
    ToolCall,
    ToolChoice,
    ToolResultMessage,
    UserMessage,
)

_ANTHROPIC_HEADERS = (
    "anthropic-version",
    "anthropic-beta",
    "anthropic-workspace-id",
    "anthropic-user-profile-id",
)
_KNOWN_PARAMETERS = frozenset({
    "model",
    "messages",
    "max_tokens",
    "cache_control",
    "container",
    "context_management",
    "diagnostics",
    "fallback_credit_token",
    "fallbacks",
    "inference_geo",
    "mcp_servers",
    "metadata",
    "output_config",
    "output_format",
    "service_tier",
    "speed",
    "stop_sequences",
    "stream",
    "system",
    "temperature",
    "thinking",
    "tool_choice",
    "tools",
    "top_k",
    "top_p",
})
_BEST_EFFORT_PARAMETERS = frozenset({
    "model",
    "messages",
    "max_tokens",
    "metadata",
    "output_config",
    "output_format",
    "service_tier",
    "stop_sequences",
    "stream",
    "system",
    "temperature",
    "thinking",
    "tool_choice",
    "tools",
    "top_p",
})


class InvalidRequest(ValueError):
    """A malformed inbound Anthropic request."""

    def __init__(self, message: str, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


@dataclass(frozen=True)
class ParsedRequest:
    model: str
    context: Context
    options: StreamOptions
    stream: bool
    native: ProtocolRequest
    ignored_parameters: tuple[str, ...]


@dataclass(frozen=True)
class ParsedCountTokensRequest:
    model: str
    native: ProtocolRequest


def _object(value: Any, param: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidRequest(f"{param} must be an object", param)
    return value


def _array(value: Any, param: str) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidRequest(f"{param} must be an array", param)
    return value


def _string(value: Any, param: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        qualifier = "non-empty " if nonempty else ""
        raise InvalidRequest(f"{param} must be a {qualifier}string", param)
    return value


def _finite_number(value: Any, param: str) -> int | float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise InvalidRequest(f"{param} must be a finite number", param)
    return cast(int | float, value)


def _validate_blocks(value: Any, param: str) -> None:
    if isinstance(value, str):
        return
    for index, raw in enumerate(_array(value, param)):
        block = _object(raw, f"{param}[{index}]")
        _string(block.get("type"), f"{param}[{index}].type", nonempty=True)


def _validate_messages(messages: Any) -> None:
    for index, raw in enumerate(_array(messages, "messages")):
        param = f"messages[{index}]"
        message = _object(raw, param)
        role = _string(message.get("role"), f"{param}.role", nonempty=True)
        if role not in ("user", "assistant", "system"):
            raise InvalidRequest(
                f"{param}.role must be user, assistant, or system",
                f"{param}.role",
            )
        _validate_blocks(message.get("content"), f"{param}.content")


def _validate_tools(tools: Any) -> None:
    for index, raw in enumerate(_array(tools, "tools")):
        tool = _object(raw, f"tools[{index}]")
        if "type" in tool:
            _string(tool["type"], f"tools[{index}].type", nonempty=True)
        elif "name" not in tool:
            raise InvalidRequest(
                f"tools[{index}] must contain name or type",
                f"tools[{index}]",
            )


def _validate_body(value: Any) -> dict[str, Any]:
    body = _object(value, "body")
    _string(body.get("model"), "model", nonempty=True)
    _validate_messages(body.get("messages"))
    max_tokens = body.get("max_tokens")
    if type(max_tokens) is not int or max_tokens < 0:
        raise InvalidRequest("max_tokens must be a non-negative integer", "max_tokens")
    if "stream" in body and not isinstance(body["stream"], bool):
        raise InvalidRequest("stream must be a boolean", "stream")

    if "system" in body:
        _validate_blocks(body["system"], "system")
    if "tools" in body:
        _validate_tools(body["tools"])
    for field in (
        "cache_control",
        "context_management",
        "diagnostics",
        "metadata",
        "output_config",
        "output_format",
        "thinking",
        "tool_choice",
    ):
        if field in body and body[field] is not None:
            _object(body[field], field)
    fallbacks = body.get("fallbacks")
    if fallbacks is not None and fallbacks != "default":
        for index, fallback in enumerate(_array(fallbacks, "fallbacks")):
            item = _object(fallback, f"fallbacks[{index}]")
            _string(item.get("model"), f"fallbacks[{index}].model", nonempty=True)
    credit = body.get("fallback_credit_token")
    if credit is not None:
        if isinstance(credit, str):
            _string(credit, "fallback_credit_token", nonempty=True)
        elif isinstance(credit, dict):
            _string(credit.get("token"), "fallback_credit_token.token", nonempty=True)
        else:
            raise InvalidRequest(
                "fallback_credit_token must be a string or object",
                "fallback_credit_token",
            )
    for field in ("mcp_servers", "stop_sequences"):
        if field in body and body[field] is not None:
            _array(body[field], field)
    if "stop_sequences" in body:
        for index, item in enumerate(body["stop_sequences"]):
            _string(item, f"stop_sequences[{index}]")
    for field in ("temperature", "top_k", "top_p"):
        if field in body and body[field] is not None:
            _finite_number(body[field], field)
    if "top_k" in body and type(body["top_k"]) is not int:
        raise InvalidRequest("top_k must be an integer", "top_k")
    if body.get("fallback_credit_token") is not None and body.get("fallbacks") is not None:
        raise InvalidRequest(
            "fallback_credit_token and fallbacks cannot be used together",
            "fallback_credit_token",
        )
    output_config = body.get("output_config")
    if (
        body.get("output_format") is not None
        and isinstance(output_config, dict)
        and output_config.get("format") is not None
    ):
        raise InvalidRequest(
            "output_format cannot be combined with output_config.format",
            "output_format",
        )
    return body


def _thinking_level(body: dict[str, Any]) -> ThinkingLevel | None:
    raw = body.get("thinking")
    if not isinstance(raw, dict) or raw.get("type") == "disabled":
        return None
    if raw.get("type") == "enabled":
        budget = raw.get("budget_tokens")
        if type(budget) is not int or budget < 0:
            return None
        thresholds = ((32768, "xhigh"), (16384, "high"), (8192, "medium"), (2048, "low"))
        return cast(ThinkingLevel, next((level for minimum, level in thresholds if budget >= minimum), "minimal"))
    if raw.get("type") == "adaptive":
        output = body.get("output_config")
        effort = output.get("effort") if isinstance(output, dict) else None
        mapping = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "xhigh"}
        return cast(ThinkingLevel, mapping.get(cast(str, effort), "medium"))
    return None


def _image(source: Any) -> ImageContent | None:
    if not isinstance(source, dict) or source.get("type") != "base64":
        return None
    data = source.get("data")
    media_type = source.get("media_type")
    if not isinstance(data, str) or not isinstance(media_type, str):
        return None
    return ImageContent(data=data, mime_type=media_type)


def _tool_result(block: dict[str, Any]) -> ToolResultMessage:
    content = block.get("content", "")
    parts: list[TextContent | ImageContent] = []
    if isinstance(content, str):
        parts.append(TextContent(text=content))
    elif isinstance(content, list):
        for raw in content:
            if not isinstance(raw, dict):
                continue
            if raw.get("type") == "text" and isinstance(raw.get("text"), str):
                parts.append(TextContent(text=raw["text"]))
            elif raw.get("type") == "image":
                image = _image(raw.get("source"))
                if image is not None:
                    parts.append(image)
    return ToolResultMessage(
        tool_call_id=block.get("tool_use_id", "") if isinstance(block.get("tool_use_id", ""), str) else "",
        content=tuple(parts),
        is_error=block.get("is_error") is True,
    )


def _parse_message(raw: dict[str, Any]) -> list[UserMessage | AssistantMessage | ToolResultMessage]:
    role = raw.get("role")
    content = raw.get("content", "")
    if role == "user":
        if isinstance(content, str):
            return [UserMessage(content=content)]
        user_parts: list[TextContent | ImageContent] = []
        results: list[ToolResultMessage] = []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                user_parts.append(TextContent(text=block["text"]))
            elif block.get("type") == "image":
                image = _image(block.get("source"))
                if image is not None:
                    user_parts.append(image)
            elif block.get("type") == "tool_result":
                results.append(_tool_result(block))
        projected: list[UserMessage | AssistantMessage | ToolResultMessage] = list(results)
        if user_parts:
            projected.append(UserMessage(content=tuple(user_parts)))
        return projected or [UserMessage(content="")]

    if isinstance(content, str):
        return [AssistantMessage(content=(TextContent(text=content),))]
    parts: list[TextContent | ThinkingContent | ToolCall] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(TextContent(text=block["text"]))
        elif block.get("type") == "thinking":
            parts.append(ThinkingContent(
                thinking=block.get("thinking", "") if isinstance(block.get("thinking", ""), str) else "",
                thinking_signature=block.get("signature") if isinstance(block.get("signature"), str) else None,
            ))
        elif block.get("type") == "redacted_thinking":
            parts.append(ThinkingContent(
                thinking=block.get("data", "") if isinstance(block.get("data", ""), str) else "",
                redacted=True,
            ))
        elif block.get("type") == "tool_use":
            arguments = block.get("input")
            parts.append(ToolCall(
                id=block.get("id", "") if isinstance(block.get("id", ""), str) else "",
                name=block.get("name", "") if isinstance(block.get("name", ""), str) else "",
                arguments=arguments if isinstance(arguments, dict) else {},
            ))
    return [AssistantMessage(content=tuple(parts))]


def _system_prompt(raw: Any) -> str | tuple[SystemPromptBlock, ...] | None:
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, list):
        return None
    blocks = tuple(
        SystemPromptBlock(
            text=block.get("text", ""),
            cache=isinstance(block.get("cache_control"), dict),
        )
        for block in raw
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    )
    return blocks or None


def _tools(raw: Any) -> tuple[Tool, ...] | None:
    if not isinstance(raw, list):
        return None
    custom = tuple(
        Tool(
            name=item.get("name", ""),
            description=item.get("description", "") if isinstance(item.get("description", ""), str) else "",
            parameters=item.get("input_schema", {}) if isinstance(item.get("input_schema", {}), dict) else {},
        )
        for item in raw
        if (
            isinstance(item, dict)
            and item.get("type") in (None, "custom")
            and isinstance(item.get("name"), str)
        )
    )
    return custom or None


def _has_web_search(raw: Any) -> bool:
    return isinstance(raw, list) and any(
        isinstance(item, dict)
        and isinstance(item.get("type"), str)
        and item["type"].startswith("web_search_")
        for item in raw
    )


def _system_message_text(raw: dict[str, Any]) -> str | None:
    if raw.get("role") != "system":
        return None
    content = raw.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    text = "\n\n".join(
        block["text"]
        for block in content
        if (
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
    )
    return text or None


def _combined_system_prompt(
    top_level: Any,
    messages: list[dict[str, Any]],
) -> str | tuple[SystemPromptBlock, ...] | None:
    prompt = _system_prompt(top_level)
    message_text = tuple(
        text
        for message in messages
        if (text := _system_message_text(message)) is not None
    )
    if not message_text:
        return prompt
    top_text = (
        prompt
        if isinstance(prompt, str)
        else "\n\n".join(block.text for block in prompt) if prompt is not None else ""
    )
    return "\n\n".join(part for part in (top_text, *message_text) if part)


def _tool_choice(raw: Any) -> ToolChoice | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind not in ("auto", "any", "tool", "none"):
        return None
    name = raw.get("name") if isinstance(raw.get("name"), str) else None
    return ToolChoice(type=cast(Any, kind), name=name)


def _response_format(body: dict[str, Any]) -> JsonSchemaFormat | None:
    raw = body.get("output_format")
    if raw is None:
        output_config = body.get("output_config")
        raw = output_config.get("format") if isinstance(output_config, dict) else None
    if not isinstance(raw, dict) or raw.get("type") != "json_schema":
        return None
    schema = raw.get("schema")
    if not isinstance(schema, dict):
        return None
    return JsonSchemaFormat.from_schema(
        schema,
        name=raw.get("name", "response") if isinstance(raw.get("name", "response"), str) else "response",
        description=raw.get("description") if isinstance(raw.get("description"), str) else None,
        strict=raw.get("strict") if isinstance(raw.get("strict"), bool) else None,
    )


def _metadata(raw: Any) -> tuple[tuple[str, str], ...] | None:
    if not isinstance(raw, dict):
        return None
    values = tuple(
        (key, value)
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, str)
    )
    return values or None


def _service_tier(raw: Any) -> str | None:
    if raw == "auto":
        return "auto"
    if raw == "standard_only":
        return "default"
    return None


def _validate_count_tokens_body(value: Any) -> dict[str, Any]:
    body = _object(value, "body")
    _string(body.get("model"), "model", nonempty=True)
    _validate_messages(body.get("messages"))
    return body


def parse_count_tokens_request(
    value: Any,
    headers: dict[str, str] | None = None,
) -> ParsedCountTokensRequest:
    """Validate a count request while preserving its complete JSON representation."""
    body = _validate_count_tokens_body(value)
    forwarded_headers = {
        name: headers[name]
        for name in _ANTHROPIC_HEADERS
        if headers is not None and name in headers
    }
    return ParsedCountTokensRequest(
        model=cast(str, body["model"]),
        native=ProtocolRequest.from_json(
            "anthropic-messages",
            body,
            headers=forwarded_headers,
            operation=NativeOperation.COUNT_TOKENS,
        ),
    )


def _estimate_count_tokens(body: dict[str, Any]) -> int:
    """Return a deterministic conservative estimate over lossless prompt JSON."""
    prompt = {key: value for key, value in body.items() if key != "model"}
    encoded = json.dumps(
        prompt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    nodes = 0
    pending: list[Any] = list(prompt.values())
    while pending:
        value = pending.pop()
        nodes += 1
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return (len(encoded) + 2) // 3 + nodes


def parse_request(value: Any, headers: dict[str, str] | None = None) -> ParsedRequest:
    """Validate an Anthropic request and retain its complete JSON representation."""
    body = _validate_body(value)
    model = cast(str, body["model"])
    raw_messages = cast(list[dict[str, Any]], body["messages"])
    messages = tuple(
        parsed
        for raw in raw_messages
        if raw.get("role") != "system"
        for parsed in _parse_message(raw)
    )
    tool_choice = body.get("tool_choice")
    parallel: bool | None = None
    if isinstance(tool_choice, dict) and "disable_parallel_tool_use" in tool_choice:
        parallel = tool_choice.get("disable_parallel_tool_use") is not True
    raw_tools = body.get("tools")
    context = Context(
        messages=messages,
        system_prompt=_combined_system_prompt(body.get("system"), raw_messages),
        tools=_tools(raw_tools),
    )
    stop_sequences = body.get("stop_sequences")
    options = StreamOptions(
        thinking=_thinking_level(body),
        temperature=body.get("temperature"),
        web_search=_has_web_search(raw_tools),
        max_tokens=body["max_tokens"],
        parallel_tool_calls=parallel,
        top_p=body.get("top_p"),
        stop_sequences=tuple(stop_sequences) if isinstance(stop_sequences, list) else None,
        tool_choice=_tool_choice(tool_choice),
        response_format=_response_format(body),
        metadata=_metadata(body.get("metadata")),
        service_tier=_service_tier(body.get("service_tier")),
    )
    forwarded_headers = {
        name: headers[name]
        for name in _ANTHROPIC_HEADERS
        if headers is not None and name in headers
    }
    ignored = projection_issues(body, _BEST_EFFORT_PARAMETERS)
    return ParsedRequest(
        model=model,
        context=context,
        options=options,
        stream=body.get("stream", False),
        native=ProtocolRequest.from_json(
            "anthropic-messages", body, headers=forwarded_headers,
        ),
        ignored_parameters=ignored,
    )
