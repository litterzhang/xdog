"""Resolve child routing/reasoning on its existing adapter; no fallback/auth/provider activation."""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import cast

from xdog.agent.core import AgentMessage, ModelStreamFn, StreamFn
from xdog.agent.image_context import validate_model_images
from xdog.agent.supervision_config import ChildConfig
from xdog.agent.supervision_images import text_size
from xdog.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Message,
    Model,
    StreamOptions,
    SystemPrompt,
    SystemPromptBlock,
    TextContent,
    ThinkingContent,
    ThinkingLevel,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    system_prompt_text,
)
from xdog.ai.utils.event_stream import EventStream


@dataclass(frozen=True, slots=True)
class ChildModel:
    id: str
    options: StreamOptions
    context_window: int
    max_prompt_tokens: int
    supports_tool_calls: bool | None
    metadata: Model | None = None
    switched: bool = False

    def to_dict(self) -> dict[str, object]:
        return {"model": self.id, "thinking": self.options.thinking or "off",
                "context_window": self.context_window, "max_prompt_tokens": self.max_prompt_tokens,
                "max_output_tokens": self.options.max_tokens, "supports_tool_calls": self.supports_tool_calls}

    def prompt(self, inherited: SystemPrompt | None) -> SystemPrompt:
        note = (f"\n\nEffective child model: {self.id}; reasoning: {self.options.thinking or 'off'}. "
                "Any parent model/environment descriptions are inherited context, not this child's active routing. "
                "Parent tool/approval/planning restrictions still apply.")
        if self.supports_tool_calls is False:
            note += " This child model has no native tool-call capability; do not claim that workspace tools executed."
        if isinstance(inherited, str) or inherited is None:
            return (inherited or "") + note
        return (*inherited, SystemPromptBlock(text=note, cache=False))

    def history(self, messages: list[AgentMessage]) -> list[AgentMessage]:
        if not self.switched:
            return messages
        # Provider/model continuity signatures cannot be transplanted to another
        # model. Preserve ordinary prose/tool pairs and their truthful origin metadata.
        result: list[AgentMessage] = []
        for message in messages:
            if isinstance(message, AssistantMessage):
                parts = tuple(replace(part, thought_signature=None) if isinstance(part, ToolCall) else
                              replace(part, text_signature=None) if isinstance(part, TextContent) else part
                              for part in message.content if not isinstance(part, ThinkingContent))
                if parts:
                    result.append(replace(message, content=parts, response_id=None))
            elif isinstance(message, UserMessage) and not isinstance(message.content, str):
                result.append(replace(message, content=tuple(
                    replace(part, text_signature=None) if isinstance(part, TextContent) else part
                    for part in message.content)))
            elif isinstance(message, ToolResultMessage):
                result.append(replace(message, content=tuple(
                    replace(part, text_signature=None) if isinstance(part, TextContent) else part
                    for part in message.content)))
            else:
                result.append(message)
        return result

    def stream(self, original: StreamFn) -> StreamFn:
        def guarded(model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
            if self.switched:
                context = replace(context, messages=tuple(cast(list[Message], self.history(list(context.messages)))))
            images = tuple(part for message in context.messages
                           if not isinstance(message.content, str)
                           for part in message.content if isinstance(part, ImageContent))
            if images:
                # Default/same-model children need the same final rendered-context
                # admission as explicitly switched ones. Resolve only when images
                # are present: plain-text custom adapters keep their existing path.
                # A live resolver refusal must not revive a withdrawn model using
                # the selection-time metadata snapshot.
                metadata = (original.resolve_model(model) if isinstance(original, ModelStreamFn)
                            else self.metadata if model == self.id else None)
                if metadata is None:
                    raise ValueError("Child image context requires advertised model metadata; no text fallback")
                if not isinstance(metadata, Model):
                    raise ValueError("Invalid child image model metadata; no request")
                if metadata.model_type != "chat" or not metadata.supports_streaming:
                    raise ValueError("Child image context requires a streaming chat model")
                validate_model_images(images, metadata)
                if isinstance(original, ModelStreamFn) and original.resolve_model(model) != metadata:
                    raise ValueError("Child image capabilities changed during admission; no request or replay")
            if self.metadata is not None:
                if self.switched:
                    # Conservative byte-based preflight, NOT exact tokenizer
                    # billing/compaction. Fail before forwarding an oversized
                    # inherited/transformed context; the user may start --fresh.
                    # Base64 transport is not model text. Keep the existing
                    # conservative text-byte guard with a rough per-image token
                    # allowance, not a false multi-megabyte text-token charge.
                    body_size = text_size(tuple(cast(list[AgentMessage], list(context.messages))))
                    image_allowance = 2048 * len(images)  # Heuristic, not provider tokenizer/billing proof.
                    schema = json.dumps([{"name": tool.name, "description": tool.description,
                                          "parameters": tool.parameters} for tool in context.tools or ()])
                    prompt_size = len((system_prompt_text(context.system_prompt) or "").encode())
                    size = body_size + image_allowance + len(schema.encode()) + prompt_size + 1024
                    limit = self.max_prompt_tokens or self.context_window - (options.max_tokens or 0)
                    if limit <= 0 or size > limit:
                        raise ValueError("Child context too large; use --fresh, lower --tokens or a larger model")
            return original(model, context, options)
        return guarded


def resolve_child_model(stream: StreamFn, parent: str, options: StreamOptions, config: ChildConfig,
                        context_window: int, supports_tool_calls: bool | None,
                        *, parent_max_prompt_tokens: int = 0) -> ChildModel:
    requested = config.model or parent
    needs_metadata = config.model is not None or config.thinking not in {None, "off"}
    metadata = stream.resolve_model(requested) if needs_metadata and isinstance(stream, ModelStreamFn) else None
    if config.model is not None and metadata is None:
        raise ValueError("Child model unavailable on existing adapter; no fallback or new authentication")
    if metadata is not None:
        ChildConfig.from_dict({"model": metadata.id})  # Reject malformed catalog identity before routing/model work.
        if not metadata.id:
            raise ValueError("Selected child model has no catalog identity")
    selected = metadata.id if config.model is not None and metadata is not None else parent
    parent_metadata = (stream.resolve_model(parent) if config.model is not None and isinstance(stream, ModelStreamFn)
                       else None)
    switched = selected != (parent_metadata.id if parent_metadata is not None else parent)
    if config.model is not None and metadata is not None:
        if metadata.model_type != "chat" or not metadata.supports_streaming:
            raise ValueError("Selected child model is not a streaming chat model")
        if metadata.context_window <= 0:
            raise ValueError("Selected child model has no advertised context limit")
    thinking = options.thinking
    if config.thinking == "off":
        thinking = None
    elif config.thinking is not None:
        if metadata is None or not metadata.reasoning:
            raise ValueError("Child model does not advertise reasoning; choose --thinking off or another model")
        thinking = cast(ThinkingLevel, config.thinking)
    elif switched and metadata is not None and not metadata.reasoning:
        thinking = None
    if (thinking is not None and metadata is not None and metadata.supported_efforts is not None
            and (switched or config.thinking is not None)):
        if thinking not in metadata.supported_efforts:
            raise ValueError("Child thinking level not advertised; choose a supported level or off")
    if switched and metadata is not None and options.web_search and metadata.supports_web_search is not True:
        raise ValueError("Selected child model does not advertise inherited native web search")
    maximum = options.max_tokens
    if metadata is not None and metadata.max_tokens > 0:
        maximum = min(maximum, metadata.max_tokens) if maximum is not None else metadata.max_tokens
    effective = replace(options, thinking=thinking, max_tokens=maximum, cancel=None,
                        prompt_cache_key=None if switched else options.prompt_cache_key)
    capability = (metadata.supports_tool_calls if config.model is not None and metadata is not None
                  else supports_tool_calls)
    if not switched and supports_tool_calls is False:
        capability = False  # A same-model alias must not undo an explicit host capability override.
    return ChildModel(selected, effective, metadata.context_window if metadata is not None else context_window,
                      metadata.max_prompt_tokens if metadata is not None else parent_max_prompt_tokens,
                      capability, metadata, switched)
