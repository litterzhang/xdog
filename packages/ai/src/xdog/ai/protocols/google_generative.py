"""Gemini message codec, independent of Google API-key or Antigravity OAuth transport."""
from __future__ import annotations

import uuid
from typing import Any

from xdog.ai.protocols._message_builder import MessageBuilder
from xdog.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    ImageContent,
    Model,
    StreamOptions,
    TextContent,
    TextDeltaEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingStartEvent,
    ToolCall,
    ToolCallDoneEvent,
    ToolCallStartEvent,
    ToolResultMessage,
    Usage,
    UserMessage,
    system_prompt_text,
)


def encode_context(context: Context, model: Model, options: StreamOptions) -> dict[str, Any]:
    contents: list[dict[str, Any]] = []
    for message in context.messages:
        parts: list[dict[str, Any]] = []
        if isinstance(message, ToolResultMessage):
            text = "\n".join(part.text for part in message.content if isinstance(part, TextContent))
            parts.append({"functionResponse": {
                "name": message.tool_name, "id": message.tool_call_id,
                "response": {"error" if message.is_error else "output": text},
            }})
            parts.extend(
                {"inlineData": {"mimeType": part.mime_type, "data": part.data}}
                for part in message.content if isinstance(part, ImageContent)
            )
            role = "user"
        else:
            role = "model" if isinstance(message, AssistantMessage) else "user"
            content = (
                (TextContent(message.content),)
                if isinstance(message, UserMessage) and isinstance(message.content, str)
                else message.content
            )
            for part in content:
                encoded: dict[str, Any]
                signature = None
                if isinstance(part, TextContent):
                    encoded = {"text": part.text}
                    signature = part.text_signature
                elif isinstance(part, ThinkingContent):
                    encoded = {"text": part.thinking, "thought": True}
                    signature = part.thinking_signature
                elif isinstance(part, ImageContent):
                    encoded = {"inlineData": {"mimeType": part.mime_type, "data": part.data}}
                elif isinstance(part, ToolCall):
                    encoded = {"functionCall": {"id": part.id, "name": part.name, "args": part.arguments}}
                    signature = part.thought_signature
                else:
                    continue
                if signature:
                    encoded["thoughtSignature"] = signature
                parts.append(encoded)
        if model.supports_image_input is False and any("inlineData" in part for part in parts):
            raise ValueError(f"Model {model.id} does not accept image input.")
        # Adjacent tool results form one Gemini user turn (parallel calls).
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})
    body: dict[str, Any] = {"contents": contents}
    system = system_prompt_text(context.system_prompt)
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    generation: dict[str, Any] = {}
    for name, value in (
        ("temperature", options.temperature), ("maxOutputTokens", options.max_tokens),
        ("topP", options.top_p), ("stopSequences", options.stop_sequences),
    ):
        if value is not None:
            generation[name] = value
    if options.thinking:
        budgets = {"minimal": 128, "low": 1024, "medium": 8192, "high": 16384, "xhigh": 32768}
        generation["thinkingConfig"] = {"includeThoughts": True, "thinkingBudget": budgets[options.thinking]}
    if options.response_format:
        generation.update({
            "responseMimeType": "application/json",
            "responseJsonSchema": options.response_format.schema(),
        })
    if generation:
        body["generationConfig"] = generation
    tools: list[dict[str, Any]] = []
    if context.tools:
        if not model.supports_tool_calls:
            raise ValueError(f"Model {model.id} does not support tool calls.")
        tools.append({"functionDeclarations": [
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
            for tool in context.tools
        ]})
    if options.web_search:
        if model.supports_web_search is False:
            raise ValueError(f"Model {model.id} does not support web search.")
        tools.append({"googleSearch": {}})
    if tools:
        body["tools"] = tools
    if options.tool_choice:
        choice = options.tool_choice
        config: dict[str, Any] = {"mode": {"auto": "AUTO", "none": "NONE", "any": "ANY", "tool": "ANY"}[choice.type]}
        if choice.type == "tool":
            if not choice.name:
                raise ValueError("Named tool choice requires a tool name.")
            config["allowedFunctionNames"] = [choice.name]
        body["toolConfig"] = {"functionCallingConfig": config}
    return body


class GeminiDecoder:
    """Accumulate Gemini chunks, preserving signatures on their content parts."""

    def __init__(self, model: Model) -> None:
        self.builder = MessageBuilder(model)

    def feed(self, raw: dict[str, Any]) -> list[AssistantMessageEvent]:
        events: list[AssistantMessageEvent] = []
        builder = self.builder
        if raw.get("error"):
            raise RuntimeError("Gemini returned an error response.")
        if raw.get("promptFeedback", {}).get("blockReason"):
            builder.stop_reason = "error"
            builder.error_message = "Gemini blocked the prompt."
        candidates = raw.get("candidates") or []
        if candidates:
            candidate = candidates[0]
            for part in candidate.get("content", {}).get("parts", []):
                signature = part.get("thoughtSignature")
                if "inlineData" in part or "inline_data" in part:
                    # The typed chat result cannot represent generated images.
                    # Never silently discard them or disguise base64 as text.
                    raise ValueError(
                        "Image output requires image_generation() or a google-generative-ai protocol-native request."
                    )
                call = part.get("functionCall")
                if isinstance(call, dict):
                    index = builder.push_block({
                        "type": "toolCall", "id": call.get("id") or f"call_{uuid.uuid4().hex}",
                        "name": call["name"], "arguments": call.get("args", {}), "thought_signature": signature,
                    })
                    block = builder.content[index]
                    events.extend((
                        ToolCallStartEvent(index=index, id=block["id"], name=block["name"], partial=builder.snapshot()),
                        ToolCallDoneEvent(
                            index=index, id=block["id"], name=block["name"], arguments=block["arguments"],
                            thought_signature=signature, partial=builder.snapshot(),
                        ),
                    ))
                elif "text" in part:
                    thinking = part.get("thought") is True
                    kind, key = ("thinking", "thinking") if thinking else ("text", "text")
                    sigkey = "thinking_signature" if thinking else "text_signature"
                    current = builder.current_block()
                    if current is None or current["type"] != kind or current.get(sigkey):
                        index = builder.push_block({"type": kind, key: ""})
                        events.append(
                            ThinkingStartEvent(index=index, partial=builder.snapshot()) if thinking
                            else TextStartEvent(index=index, partial=builder.snapshot())
                        )
                    index = builder.block_index
                    block = builder.content[index]
                    block[key] += part["text"]
                    if signature:
                        block[sigkey] = signature
                    builder.mark_dirty()
                    events.append(
                        ThinkingDeltaEvent(index=index, delta=part["text"], partial=builder.snapshot()) if thinking
                        else TextDeltaEvent(index=index, delta=part["text"], partial=builder.snapshot())
                    )
                elif signature and builder.current_block() is not None:
                    block = builder.content[-1]
                    field = {
                        "text": "text_signature", "thinking": "thinking_signature", "toolCall": "thought_signature",
                    }[block["type"]]
                    block[field] = signature
            reason = candidate.get("finishReason")
            if reason == "MAX_TOKENS":
                builder.stop_reason = "length"
            elif reason and reason != "STOP":
                builder.stop_reason = "error"
                builder.error_message = f"Gemini stopped generation: {reason}"
            elif any(block["type"] == "toolCall" for block in builder.content):
                builder.stop_reason = "toolUse"
        usage = raw.get("usageMetadata")
        if isinstance(usage, dict):
            cached = int(usage.get("cachedContentTokenCount", 0))
            builder.usage = Usage(
                input=max(0, int(usage.get("promptTokenCount", 0)) - cached),
                output=int(usage.get("candidatesTokenCount", 0)) + int(usage.get("thoughtsTokenCount", 0)),
                cache_read=cached, total_tokens=int(usage.get("totalTokenCount", 0)),
            )
        builder.response_id = raw.get("responseId", builder.response_id)
        builder.mark_dirty()
        return events
