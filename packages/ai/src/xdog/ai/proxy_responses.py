"""Stateless OpenAI Responses facade (inbound), independent of provider protocols.

Only features representable by Context/StreamOptions are accepted. In particular,
this adapter never pretends to persist responses or execute hosted tools.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import math
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any, cast

from xdog.ai.types import (
    AssistantContentPart,
    AssistantMessage,
    Context,
    ImageContent,
    Message,
    StreamOptions,
    TextContent,
    TextVerbosity,
    ThinkingContent,
    ThinkingLevel,
    Tool,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

logger = logging.getLogger(__name__)


class ResponseFormatError(ValueError):
    """Invalid generated response metadata, not a retryable network disconnect."""


_REASONING_ID_PREFIX = "rs_xdog_v1_"


def _client_reasoning_id(original: str) -> str:
    # Codex strips unprefixed item IDs before replay. Copilot may use opaque IDs
    # containing +, /, and =. Encode those losslessly, not as new random IDs.
    if re.fullmatch(r"rs_[A-Za-z0-9_-]+", original) and not original.startswith(_REASONING_ID_PREFIX):
        return original
    return _REASONING_ID_PREFIX + base64.urlsafe_b64encode(original.encode()).decode().rstrip("=")


def _upstream_reasoning_id(value: str, param: str) -> str:
    if not value.startswith(_REASONING_ID_PREFIX):
        return value
    encoded = value[len(_REASONING_ID_PREFIX):]
    try:
        original = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True).decode()
        if not original or _client_reasoning_id(original) != value:
            raise ValueError("Noncanonical reasoning ID")
        return original
    except (ValueError, UnicodeError) as exc:
        raise InvalidRequest("Invalid proxy reasoning item id", param) from exc


class InvalidRequest(ValueError):
    def __init__(self, message: str, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


def error_body(message: str, *, kind: str = "invalid_request_error", param: str | None = None) -> dict[str, Any]:
    return {"error": {"message": message, "type": kind, "param": param, "code": None}}


def _object(value: Any, param: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidRequest(f"{param} must be an object", param)
    return value


def _string(value: Any, param: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise InvalidRequest(f"{param} must be a {'non-empty ' if nonempty else ''}string", param)
    return value


def _list(value: Any, param: str) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidRequest(f"{param} must be an array", param)
    return value


def _content(value: Any, param: str, *, images: bool = False) -> tuple[TextContent | ImageContent, ...]:
    if isinstance(value, str):
        return (TextContent(text=value),)
    parts: list[TextContent | ImageContent] = []
    for raw in _list(value, param):
        block = _object(raw, param)
        kind = block.get("type")
        if kind in ("input_text", "output_text"):
            parts.append(TextContent(text=_string(block.get("text"), param)))
        elif kind == "input_image" and images:
            url = _string(block.get("image_url"), param)
            header, separator, data = url.partition(",")
            if not separator or not header.startswith("data:image/") or not header.endswith(";base64"):
                raise InvalidRequest("Only base64 data URLs are supported for input_image", param)
            try:
                base64.b64decode(data, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise InvalidRequest("Invalid base64 image data", param) from exc
            parts.append(ImageContent(data=data, mime_type=header[5:-7]))
        else:
            raise InvalidRequest(f"Unsupported content type: {kind!r}", param)
    return tuple(parts)


def _text(value: Any, param: str) -> str:
    return "\n".join(p.text for p in _content(value, param) if isinstance(p, TextContent))


@dataclass(frozen=True)
class _ToolBinding:
    name: str
    namespace: str | None
    kind: str
    tool: Tool
    wire: dict[str, Any]


@dataclass(frozen=True)
class _ToolRegistry:
    bindings: dict[str, _ToolBinding]
    wire: list[dict[str, Any]]

    @property
    def tools(self) -> tuple[Tool, ...]:
        return tuple(binding.tool for binding in self.bindings.values())


def _tool_name(name: str, namespace: str | None) -> str:
    if namespace is None:
        return name
    # Stable across requests and independent of the currently loaded tool set.
    # Names remain valid for all upstreams (ASCII, <=64 chars). Detect collisions
    # with user-supplied flat tool names in the registry rather than aliasing them.
    identity = json.dumps([namespace, name], ensure_ascii=True, separators=(",", ":"))
    return "xdog_ns_" + hashlib.sha256(identity.encode()).hexdigest()[:56]


def _history_tool_name(item: dict[str, Any], param: str) -> str:
    name = _string(item.get("name"), f"{param}.name", nonempty=True)
    namespace = item.get("namespace")
    if namespace is not None:
        namespace = _string(namespace, f"{param}.namespace", nonempty=True)
    return _tool_name(name, namespace)


def _parse_tool(
    raw: Any, param: str, namespace: str | None = None, namespace_description: str = "",
) -> _ToolBinding:
    wire = _object(raw, param)
    kind = wire.get("type")
    if kind not in ("function", "custom", "tool_search"):
        raise InvalidRequest(
            f"Unsupported tool type: {kind!r}; expected a function, custom, or client tool_search tool",
            f"{param}.type",
        )
    if kind == "tool_search" and (wire.get("execution") != "client" or namespace is not None):
        raise InvalidRequest("Only top-level client-executed tool_search is supported", f"{param}.execution")
    name = "tool_search" if kind == "tool_search" else _string(wire.get("name"), f"{param}.name", nonempty=True)
    description = _string(wire.get("description", ""), f"{param}.description")
    if namespace is not None:
        description = f"Tool {namespace}.{name}.\n{namespace_description}\n{description}"
    if kind in ("function", "tool_search"):
        if wire.get("strict") is not None and wire["strict"] is not False:
            raise InvalidRequest("Strict function schemas are unsupported", f"{param}.strict")
        parameters = _object(wire.get("parameters", {"type": "object", "properties": {}}), f"{param}.parameters")
    else:
        # Adapt freeform tools to the provider-neutral function interface, never
        # execute them here. Their client-visible input remains a raw string.
        fmt = _object(wire.get("format", {"type": "text"}), f"{param}.format")
        if fmt.get("type") == "grammar":
            if fmt.get("syntax") not in ("lark", "regex"):
                raise InvalidRequest("Custom grammar syntax must be lark or regex", f"{param}.format.syntax")
            grammar = _string(fmt.get("definition"), f"{param}.format.definition", nonempty=True)
            description += f"\nThe raw input must follow this {fmt['syntax']} grammar:\n{grammar}"
        elif fmt.get("type") != "text":
            raise InvalidRequest("Custom format must be text or grammar", f"{param}.format.type")
        description += (
            "\nSupply the raw tool input as the string in the input argument, without a JSON wrapper inside it."
        )
        parameters = {"type": "object", "properties": {"input": {"type": "string"}},
                      "required": ["input"], "additionalProperties": False}
    return _ToolBinding(
        name, namespace, kind,
        Tool(name=_tool_name(name, namespace), description=description, parameters=parameters), wire,
    )


def _collect_tools(body: dict[str, Any]) -> _ToolRegistry:
    """Normalize conventional tools and Codex Responses Lite's additional_tools.

    Namespaces stay intact on the client-facing wire. The provider-neutral runtime
    gets flat function tools, with reversible name bindings for replies and replay.
    """
    sources: list[tuple[Any, str]] = [(body.get("tools") if body.get("tools") is not None else [], "tools")]
    input_items = body.get("input")
    if isinstance(input_items, list):
        for index, item in enumerate(input_items):
            if isinstance(item, dict) and item.get("type") == "additional_tools":
                param = f"input[{index}]"
                if item.get("role") not in ("developer", "system"):
                    raise InvalidRequest("additional_tools.role must be developer or system", f"{param}.role")
                sources.append((item.get("tools"), f"{param}.tools"))
            elif isinstance(item, dict) and item.get("type") == "tool_search_output":
                param = f"input[{index}]"
                if item.get("execution") != "client":
                    raise InvalidRequest("Only client tool_search_output is supported", f"{param}.execution")
                sources.append((item.get("tools"), f"{param}.tools"))

    bindings: dict[str, _ToolBinding] = {}
    wire: list[dict[str, Any]] = []
    namespaces: dict[str, dict[str, Any]] = {}

    def add(raw: Any, param: str, namespace: str | None = None, description: str = "") -> None:
        binding = _parse_tool(raw, param, namespace, description)
        existing = bindings.get(binding.tool.name)
        if existing is not None:
            if (existing.namespace, existing.name) != (namespace, binding.name):
                raise InvalidRequest("Tool name collides with a namespaced tool's internal name", f"{param}.name")
            if existing.kind != binding.kind or existing.tool != binding.tool:
                raise InvalidRequest(f"Conflicting definitions for tool {binding.name!r}", f"{param}.name")
            return
        bindings[binding.tool.name] = binding
        if namespace is None:
            wire.append(binding.wire)
        else:
            namespaces[namespace]["tools"].append(binding.wire)

    for values, source in sources:
        for index, raw in enumerate(_list(values, source)):
            param = f"{source}[{index}]"
            spec = _object(raw, param)
            if spec.get("type") != "namespace":
                add(spec, param)
                continue
            namespace = _string(spec.get("name"), f"{param}.name", nonempty=True)
            description = _string(spec.get("description", ""), f"{param}.description")
            children = _list(spec.get("tools"), f"{param}.tools")
            if namespace not in namespaces:
                # Copy the group/list, so repeated requests cannot mutate history.
                namespaces[namespace] = {**spec, "tools": []}
                wire.append(namespaces[namespace])
            elif namespaces[namespace].get("description", "") != description:
                raise InvalidRequest(f"Conflicting descriptions for namespace {namespace!r}", f"{param}.description")
            for child_index, child in enumerate(children):
                add(child, f"{param}.tools[{child_index}]", namespace, description)
    return _ToolRegistry(bindings, wire)


def parse_request(body: Any) -> tuple[str, Context, StreamOptions, bool]:
    body = _object(body, "body")
    model = _string(body.get("model"), "model", nonempty=True)
    allowed = {
        "model", "input", "instructions", "stream", "store", "background", "previous_response_id", "conversation",
        "tools", "tool_choice", "parallel_tool_calls", "max_output_tokens", "temperature",
        "reasoning", "text", "include", "client_metadata", "prompt_cache_key",
        "metadata", "truncation", "service_tier", "top_p", "max_tool_calls", "prompt", "stream_options",
    }
    for key in body.keys() - allowed:
        raise InvalidRequest(f"Unsupported request parameter: {key}", key)
    # Coding clients attach transport/session metadata here. It is not model
    # input or persisted response metadata, so validate the container but do
    # not forward, echo, or apply the public `metadata` field's size limits.
    if body.get("client_metadata") is not None:
        _object(body["client_metadata"], "client_metadata")
    prompt_cache_key = body.get("prompt_cache_key")
    if prompt_cache_key is not None:
        _string(prompt_cache_key, "prompt_cache_key")
    if "metadata" in body:
        metadata = _object(body["metadata"], "metadata")
        if len(metadata) > 16 or any(
            len(key) > 64 or not isinstance(value, str) or len(value) > 512 for key, value in metadata.items()
        ):
            raise InvalidRequest("metadata permits up to 16 string entries (64-character keys, 512-character values)",
                                 "metadata")
    for key in ("stream", "store", "background", "parallel_tool_calls"):
        if key in body and not isinstance(body[key], bool):
            raise InvalidRequest(f"{key} must be a boolean", key)
    for key in ("previous_response_id", "conversation"):
        if body.get(key) is not None:
            raise InvalidRequest(f"{key} is unsupported; send full history in input instead", key)
    for key in ("store", "background"):
        if body.get(key):
            raise InvalidRequest(f"{key}=true is unsupported by this stateless proxy", key)
    # Do not silently discard generation controls the runtime cannot honour.
    defaults: dict[str, Any] = {
        "tool_choice": "auto", "truncation": "disabled", "service_tier": "auto",
    }
    for key, default in defaults.items():
        if key in body and body[key] != default:
            raise InvalidRequest(f"Only {key}={default!r} is supported", key)
    for key in ("top_p", "max_tool_calls", "prompt", "stream_options"):
        if body.get(key) is not None:
            raise InvalidRequest(f"{key} is unsupported", key)
    if "include" in body:
        for item in _list(body["include"], "include"):
            if item != "reasoning.encrypted_content":
                raise InvalidRequest(f"Unsupported include: {item!r}", "include")
    verbosity = None
    if body.get("text") is not None:
        text = _object(body["text"], "text")
        if _object(text.get("format", {"type": "text"}), "text.format") != {"type": "text"}:
            raise InvalidRequest("Only text.format.type=text is supported", "text.format")
        verbosity = text.get("verbosity")
        if verbosity not in (None, "low", "medium", "high"):
            raise InvalidRequest("text.verbosity must be low, medium, or high", "text.verbosity")

    instructions: list[str] = []
    if body.get("instructions") is not None:
        instructions.append(_string(body["instructions"], "instructions"))
    messages: list[Message] = []
    call_names: dict[str, str] = {}

    def assistant(parts: tuple[AssistantContentPart, ...]) -> None:
        # A Responses turn consists of separate reasoning/message/function items.
        # Keep them together or the upstream adapters may cancel parallel calls.
        if messages and isinstance(messages[-1], AssistantMessage):
            messages[-1] = replace(messages[-1], content=messages[-1].content + parts)
        else:
            messages.append(AssistantMessage(content=parts, api="openai-responses"))

    raw_input = body.get("input")
    if isinstance(raw_input, str):
        messages.append(UserMessage(content=raw_input))
    else:
        for index, raw in enumerate(_list(raw_input, "input")):
            param = f"input[{index}]"
            item = _object(raw, param)
            kind = item.get("type", "message")
            if kind == "additional_tools":
                # Collected and validated below, never injected as conversation text.
                continue
            if kind == "message":
                role = item.get("role")
                content = item.get("content")
                if role in ("system", "developer"):
                    instructions.append(_text(content, param))
                elif role == "user":
                    messages.append(UserMessage(content=_content(content, param, images=True)))
                elif role == "assistant":
                    assistant(tuple(p for p in _content(content, param) if isinstance(p, TextContent)))
                else:
                    raise InvalidRequest(f"Unsupported message role: {role!r}", param)
            elif kind == "tool_search_call":
                if item.get("execution") != "client":
                    raise InvalidRequest("Only client tool_search_call is supported", f"{param}.execution")
                call_id = _string(item.get("call_id"), f"{param}.call_id", nonempty=True)
                call_names[call_id] = "tool_search"
                assistant((ToolCall(id=call_id, name="tool_search", arguments=_object(
                    item.get("arguments"), f"{param}.arguments",
                )),))
            elif kind == "tool_search_output":
                call_id = _string(item.get("call_id"), f"{param}.call_id", nonempty=True)
                discovered = _list(item.get("tools"), f"{param}.tools")
                messages.append(ToolResultMessage(
                    tool_call_id=call_id, tool_name="tool_search",
                    content=(TextContent(text=json.dumps({"tools": discovered})),),
                ))
            elif kind in ("function_call", "custom_tool_call"):
                call_id = _string(item.get("call_id"), f"{param}.call_id", nonempty=True)
                name = _history_tool_name(item, param)
                call_names[call_id] = name
                if kind == "custom_tool_call":
                    assistant((ToolCall(id=call_id, name=name, arguments={
                        "input": _string(item.get("input"), f"{param}.input"),
                    }),))
                    continue
                arguments_text = _string(item.get("arguments"), f"{param}.arguments")
                try:
                    arguments = json.loads(arguments_text)
                except json.JSONDecodeError as exc:
                    raise InvalidRequest("function_call.arguments must be valid JSON", param) from exc
                assistant((ToolCall(
                    id=call_id, name=name,
                    arguments=_object(arguments, f"{param}.arguments"),
                ),))
            elif kind in ("function_call_output", "custom_tool_call_output"):
                call_id = _string(item.get("call_id"), f"{param}.call_id", nonempty=True)
                # Custom outputs may include a name but no namespace. The
                # matching call id is authoritative for recovering its identity.
                result_name = call_names.get(call_id)
                if result_name is None:
                    result_name = _history_tool_name(item, param) if item.get("name") is not None else ""
                messages.append(ToolResultMessage(
                    tool_call_id=call_id, tool_name=result_name,
                    content=(TextContent(text=_text(item.get("output"), f"{param}.output")),),
                ))
            elif kind == "reasoning":
                summary = _list(item.get("summary", []), f"{param}.summary")
                texts = []
                for raw_part in summary:
                    part = _object(raw_part, param)
                    if part.get("type") != "summary_text":
                        raise InvalidRequest("Only summary_text reasoning parts are supported", param)
                    texts.append(_string(part.get("text"), param))
                encrypted = item.get("encrypted_content")
                if encrypted is not None:
                    _string(encrypted, f"{param}.encrypted_content")
                    item_id = _string(item.get("id"), f"{param}.id", nonempty=True)
                    item = {**item, "id": _upstream_reasoning_id(item_id, f"{param}.id")}
                assistant((ThinkingContent(
                    thinking="\n".join(texts),
                    thinking_signature=json.dumps(item) if encrypted else None,
                ),))
            else:
                raise InvalidRequest(f"Unsupported input item type: {kind!r}", param)

    registry = _collect_tools(body)

    reasoning = _object(body["reasoning"] if body.get("reasoning") is not None else {}, "reasoning")
    effort = reasoning.get("effort")
    if effort is not None and effort not in ("minimal", "low", "medium", "high", "xhigh"):
        raise InvalidRequest("Unsupported reasoning effort", "reasoning.effort")
    if reasoning.get("summary") not in (None, "auto", "concise", "detailed"):
        raise InvalidRequest("Unsupported reasoning summary", "reasoning.summary")
    max_tokens = body.get("max_output_tokens")
    if max_tokens is not None and (type(max_tokens) is not int or max_tokens < 1):
        raise InvalidRequest("max_output_tokens must be a positive integer", "max_output_tokens")
    temperature = body.get("temperature")
    if temperature is not None and (
        type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 2
    ):
        raise InvalidRequest("temperature must be a number between 0 and 2", "temperature")
    context = Context(
        messages=tuple(messages), system_prompt="\n\n".join(instructions) or None, tools=registry.tools or None,
    )
    options = StreamOptions(
        thinking=cast(ThinkingLevel | None, effort), max_tokens=max_tokens, temperature=temperature,
        prompt_cache_key=prompt_cache_key, parallel_tool_calls=body.get("parallel_tool_calls"),
        verbosity=cast(TextVerbosity | None, verbosity),
    )
    return model, context, options, body.get("stream", False)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"



def _native_reasoning(part: ThinkingContent) -> dict[str, Any] | None:
    if part.thinking_signature:
        try:
            signature = json.loads(part.thinking_signature)
        except (ValueError, TypeError):
            return None
        if isinstance(signature, dict) and signature.get("type") == "reasoning":
            return signature
    return None


def _output_item(
    part: AssistantContentPart, registry: _ToolRegistry | None = None, *, partial: bool = False,
) -> dict[str, Any]:
    if isinstance(part, TextContent):
        return {"type": "message", "id": _id("msg"), "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": part.text, "annotations": [], "logprobs": []}]}
    if isinstance(part, ToolCall):
        call_id, _, item_id = part.id.partition("|")
        binding = registry.bindings.get(part.name) if registry else None
        tool_item: dict[str, Any] = {
            "type": "function_call", "id": item_id or _id("fc"), "call_id": call_id,
            "name": binding.name if binding else part.name, "arguments": json.dumps(part.arguments),
            "status": "completed",
        }
        if binding and binding.namespace is not None:
            tool_item["namespace"] = binding.namespace
        if binding and binding.kind == "tool_search":
            tool_item.update(type="tool_search_call", id=_id("tsc"), execution="client", arguments=part.arguments)
            tool_item.pop("name")
        if binding and binding.kind == "custom":
            raw_input = "" if partial else part.arguments.get("input")
            if not isinstance(raw_input, str):
                raise ResponseFormatError("Upstream custom tool call must contain a string input argument")
            tool_item.update(type="custom_tool_call", id=_id("ctc"), input=raw_input)
            tool_item.pop("arguments")
        return tool_item
    item: dict[str, Any] = {"type": "reasoning", "id": _id("rs"), "summary": []}
    if part.thinking and not part.redacted:
        item["summary"] = [{"type": "summary_text", "text": part.thinking}]
    signature = _native_reasoning(part)
    if signature is not None:
        original_id = signature.get("id")
        if isinstance(signature.get("encrypted_content"), str) and (
            not isinstance(original_id, str) or not original_id
        ):
            raise ResponseFormatError("Upstream encrypted reasoning is missing its original item id")
        for key in ("id", "encrypted_content"):
            if isinstance(signature.get(key), str):
                item[key] = _client_reasoning_id(signature[key]) if key == "id" else signature[key]
    return item


def _usage(usage: Usage) -> dict[str, Any]:
    # Internal input excludes cache reads/writes; OpenAI input includes both.
    input_tokens = usage.input + usage.cache_read + usage.cache_write
    return {"input_tokens": input_tokens, "input_tokens_details": {"cached_tokens": usage.cache_read},
            "output_tokens": usage.output, "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": input_tokens + usage.output}


def format_response(
    msg: AssistantMessage, model: str, request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request = request or {}
    registry = _collect_tools(request)
    status = "incomplete" if msg.stop_reason == "length" else (
        "failed" if msg.stop_reason in ("error", "aborted") else "completed"
    )
    return {
        "id": _id("resp"), "object": "response", "created_at": int(time.time()), "model": model,
        "status": status, "output": [_output_item(part, registry) for part in msg.content], "usage": _usage(msg.usage),
        "error": {"code": "server_error", "message": msg.error_message or "Generation failed"}
        if status == "failed" else None,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "store": False, "background": False, "previous_response_id": None,
        "parallel_tool_calls": request.get("parallel_tool_calls", True),
        "tool_choice": "auto", "tools": registry.wire,
        "instructions": request.get("instructions"), "max_output_tokens": request.get("max_output_tokens"),
        "temperature": request.get("temperature"), "top_p": None, "metadata": request.get("metadata", {}),
        "text": {"format": {"type": "text"}, "verbosity": "medium", **(request.get("text") or {})},
        "reasoning": request.get("reasoning"),
        "prompt_cache_key": request.get("prompt_cache_key"),
    }


class _ResponseStream:
    """One response with stable item IDs and a monotonically increasing sequence."""

    def __init__(self, model: str, request: dict[str, Any] | None = None) -> None:
        self.registry = _collect_tools(request or {})
        self.response = format_response(AssistantMessage(), model, request)
        self.response.update(status="in_progress", usage=None)
        self.items: dict[int, dict[str, Any]] = {}
        self.indices: dict[int, int] = {}
        self.closed: set[int] = set()
        self.sequence = 0
        self.reasoning_ids: dict[int, str] = {}
        self.reasoning_parts: dict[int, ThinkingContent] = {}

    def observe_reasoning(self, event: Any) -> None:
        def remember(index: int, part: ThinkingContent, finalized: bool) -> None:
            signature = _native_reasoning(part)
            original_id = signature.get("id") if signature else None
            if finalized and isinstance(original_id, str) and original_id:
                previous = self.reasoning_ids.get(index)
                emitted = self.items.get(index)
                if (previous is not None and previous != original_id) or (
                    emitted and emitted["id"] != _client_reasoning_id(original_id)
                ):
                    raise ResponseFormatError("Upstream reasoning item id changed after finalization")
                self.reasoning_ids[index] = original_id
                self.reasoning_parts[index] = part
            elif index not in self.reasoning_ids:
                self.reasoning_parts[index] = part

        message = getattr(event, "partial", None) or getattr(event, "message", None)
        if message is not None:
            for index, part in enumerate(message.content):
                if isinstance(part, ThinkingContent):
                    finalized = event.type in ("done", "error") or (
                        event.type == "thinking_done" and index == event.index
                    )
                    remember(index, part, finalized)
        if event.type == "thinking_done" and (event.thinking_signature is not None or message is None):
            remember(event.index, ThinkingContent(
                thinking=event.thinking, thinking_signature=event.thinking_signature, redacted=event.redacted,
            ), finalized=True)

    def event(self, event_type: str, **data: Any) -> bytes:
        payload = {"type": event_type, "sequence_number": self.sequence, **data}
        self.sequence += 1
        return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n".encode()

    def add(self, index: int, part: AssistantContentPart) -> list[bytes]:
        if index in self.items:
            return []
        item = _output_item(part, self.registry, partial=True)
        if isinstance(part, ThinkingContent) and index in self.reasoning_ids:
            item["id"] = _client_reasoning_id(self.reasoning_ids[index])
        kind = item["type"]
        if kind == "message":
            item.update(status="in_progress", content=[])
        elif kind in ("function_call", "custom_tool_call", "tool_search_call"):
            item.update(status="in_progress")
            if kind == "tool_search_call":
                item["arguments"] = {}
            else:
                item["arguments" if kind == "function_call" else "input"] = ""
        else:
            item["summary"] = []
            item.pop("encrypted_content", None)
        self.items[index] = item
        self.indices[index] = len(self.response["output"])
        self.response["output"].append(item)
        events = [self.event("response.output_item.added", output_index=self.indices[index], item=item)]
        if kind not in ("function_call", "custom_tool_call", "tool_search_call"):
            block = {"type": "output_text", "text": "", "annotations": [], "logprobs": []} if kind == "message" else {
                "type": "summary_text", "text": "",
            }
            item["content" if kind == "message" else "summary"].append(block)
            events.append(self.event(
                "response.content_part.added" if kind == "message" else "response.reasoning_summary_part.added",
                item_id=item["id"], output_index=self.indices[index], part=block,
                **{"content_index" if kind == "message" else "summary_index": 0},
            ))
        return events

    def delta(self, index: int, delta: str) -> bytes | None:
        item = self.items[index]
        kind = item["type"]
        data: dict[str, Any] = {"item_id": item["id"], "output_index": self.indices[index], "delta": delta}
        if kind in ("custom_tool_call", "tool_search_call"):
            # Upstream deltas contain JSON for our string-input wrapper. Wait
            # for the final parsed arguments, never leak JSON escapes as raw input.
            return None
        if kind == "function_call":
            item["arguments"] += delta
            name = "response.function_call_arguments.delta"
        else:
            item["content" if kind == "message" else "summary"][0]["text"] += delta
            data["content_index" if kind == "message" else "summary_index"] = 0
            if kind == "message":
                data["logprobs"] = []
            name = "response.output_text.delta" if kind == "message" else "response.reasoning_summary_text.delta"
        return self.event(name, **data)

    def finish(self, index: int, part: AssistantContentPart) -> list[bytes]:
        if isinstance(part, ThinkingContent) and index in self.reasoning_ids:
            # Metadata may arrive only in a later snapshot or terminal message.
            part = replace(part, thinking_signature=self.reasoning_parts[index].thinking_signature)
        # Validate the final payload before allocating any sequenced events.
        final = _output_item(part, self.registry)
        if index in self.closed:
            if isinstance(part, ThinkingContent) and "encrypted_content" in final:
                if final["id"] != self.items[index]["id"]:
                    raise ResponseFormatError("Cannot rebind encrypted reasoning to another item id")
                self.items[index]["encrypted_content"] = final["encrypted_content"]
            return []
        events = self.add(index, part)
        item = self.items[index]
        if isinstance(part, ThinkingContent) and index in self.reasoning_ids and final["id"] != item["id"]:
            raise ResponseFormatError("Cannot rebind encrypted reasoning to another item id")
        final["id"] = item["id"]
        kind = item["type"]
        if kind == "tool_search_call":
            # The native search call carries an arguments object, not JSON-text
            # deltas. Its completed output item is enough to dispatch the client tool.
            item.update(final)
            events.append(self.event("response.output_item.done", output_index=self.indices[index], item=item))
            self.closed.add(index)
            return events
        # Some providers only supply final content, without deltas.
        if kind in ("function_call", "custom_tool_call"):
            field = "arguments" if kind == "function_call" else "input"
            current, text = item[field], final[field]
        else:
            field = "content" if kind == "message" else "summary"
            current = item[field][0]["text"]
            text = final[field][0]["text"] if final[field] else ""
        if text.startswith(current) and text != current:
            if kind == "custom_tool_call":
                events.append(self.event("response.custom_tool_call_input.delta", item_id=item["id"],
                                         output_index=self.indices[index], delta=text[len(current):]))
            else:
                delta = self.delta(index, text[len(current):])
                if delta is not None:
                    events.append(delta)
        item.update(final)
        data = {"item_id": item["id"], "output_index": self.indices[index]}
        if kind in ("function_call", "custom_tool_call"):
            if "namespace" in item:
                data["namespace"] = item["namespace"]
            if kind == "custom_tool_call":
                events.append(self.event("response.custom_tool_call_input.done", **data, input=item["input"]))
            else:
                events.append(self.event("response.function_call_arguments.done", **data,
                                         name=item["name"], arguments=item["arguments"]))
        else:
            field = "content" if kind == "message" else "summary"
            block = item[field][0] if item[field] else {"type": "summary_text", "text": ""}
            data["content_index" if kind == "message" else "summary_index"] = 0
            events.append(self.event(
                "response.output_text.done" if kind == "message" else "response.reasoning_summary_text.done",
                **data, text=block["text"], **({"logprobs": []} if kind == "message" else {}),
            ))
            events.append(self.event(
                "response.content_part.done" if kind == "message" else "response.reasoning_summary_part.done",
                **data, part=block,
            ))
        events.append(self.event("response.output_item.done", output_index=self.indices[index], item=item))
        self.closed.add(index)
        return events

    def terminal(self, msg: AssistantMessage) -> bytes:
        final = format_response(msg, self.response["model"])
        for key in ("status", "usage", "error", "incomplete_details"):
            self.response[key] = final[key]
        return self.event(f"response.{self.response['status']}", response=self.response)



async def _reasoning_identity_events(event_stream: Any, state: _ResponseStream) -> AsyncIterator[Any]:
    """Wait for the finalized reasoning identity, then preserve event order.

    Copilot can replace even an encrypted provisional ID at item.done. Buffer
    the reasoning block until that final snapshot rather than exposing an ID
    that cannot later be rebound. Keep delta text, not quadratic snapshots.
    """
    pending: list[Any] = []
    unresolved: set[int] = set()
    async for event in event_stream:
        state.observe_reasoning(event)
        if event.type.startswith("thinking_") and event.index not in state.reasoning_ids:
            unresolved.add(event.index)
            if event.type == "thinking_done":
                part = state.reasoning_parts.get(event.index)
                partial = getattr(event, "partial", None)
                if (part and part.thinking_signature and _native_reasoning(part) is None) or (
                    partial and partial.api and partial.api != "openai-responses"
                ):
                    # Anthropic/Chat Completions reasoning has no Responses item
                    # identity. It can use a generated ID once the block ends.
                    unresolved.discard(event.index)
        unresolved.difference_update(state.reasoning_ids)
        if event.type in ("done", "error"):
            unresolved.clear()
        if pending or unresolved:
            if event.type.endswith("_delta") and getattr(event, "partial", None) is not None:
                event = replace(event, partial=None)
            pending.append(event)
            if not unresolved:
                for buffered in pending:
                    yield buffered
                pending.clear()
        else:
            yield event
    # On premature EOF, partial reasoning has no encrypted metadata to bind.
    # Preserve it before the caller emits response.failed.
    for buffered in pending:
        yield buffered


async def stream_to_sse(
    event_stream: Any, model: str, request: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    state = _ResponseStream(model, request)
    yield state.event("response.created", response=state.response)
    yield state.event("response.in_progress", response=state.response)
    try:
        async for event in _reasoning_identity_events(event_stream, state):
            kind = event.type
            if kind == "done":
                msg = event.message or AssistantMessage(stop_reason=event.stop_reason)
                for index, final_part in enumerate(msg.content):
                    for chunk in state.finish(index, final_part):
                        yield chunk
                yield state.terminal(msg)
                return
            if kind == "error":
                failed = replace(event.message, stop_reason="error", error_message=event.error) if event.message else (
                    AssistantMessage(stop_reason="error", error_message=event.error)
                )
                yield state.terminal(failed)
                return
            if kind in ("start", "usage", "status"):
                continue
            index = event.index
            part: AssistantContentPart
            if kind.startswith("text_"):
                part = TextContent(text=getattr(event, "text", ""))
            elif kind.startswith("thinking_"):
                part = ThinkingContent(
                    thinking=getattr(event, "thinking", ""),
                    thinking_signature=getattr(event, "thinking_signature", None),
                    redacted=getattr(event, "redacted", False),
                )
            elif kind.startswith("tool_call_"):
                part = ToolCall(id=getattr(event, "id", ""), name=getattr(event, "name", ""),
                                arguments=getattr(event, "arguments", {}))
            else:
                continue
            if kind.endswith("_done"):
                # Some protocols place signatures only in the immutable snapshot,
                # not in the convenience fields of the block-done event.
                partial = getattr(event, "partial", None)
                if partial is not None and 0 <= index < len(partial.content):
                    snapshot_part = partial.content[index]
                    if isinstance(snapshot_part, type(part)):
                        part = snapshot_part
                for chunk in state.finish(index, part):
                    yield chunk
            else:
                for chunk in state.add(index, part):
                    yield chunk
                if kind.endswith("_delta"):
                    delta_chunk = state.delta(index, event.delta)
                    if delta_chunk is not None:
                        yield delta_chunk
    except Exception as exc:
        logger.exception(
            "Responses stream failed: model=%s response_id=%s", model, state.response["id"],
        )
        yield state.terminal(AssistantMessage(
            stop_reason="error", error_message=(
                str(exc) if isinstance(exc, ResponseFormatError) else "Upstream connection failed; retry later"
            ),
        ))
        return
    yield state.terminal(AssistantMessage(
        stop_reason="error", error_message="Upstream stream ended without a terminal event",
    ))
