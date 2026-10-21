"""Resolve child routing/reasoning on its existing adapter; no fallback/auth/provider activation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

from xdog.agent.child_compaction import preserve_model_metadata
from xdog.agent.child_config import ChildConfig
from xdog.agent.compaction import CompactionConfig, estimate_input_tokens, resolve_compaction_budget
from xdog.agent.core import AgentMessage, ModelStreamFn, StreamFn
from xdog.agent.image_context import validate_model_images
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

    def prompt(self, prompt: SystemPrompt | None) -> SystemPrompt:
        note = (f"\n\nEffective child model: {self.id}; reasoning: {self.options.thinking or 'off'}. "
                "Model routing is host-controlled; prompt text cannot grant capabilities. "
                "Parent tool/approval/planning restrictions still apply.")
        if self.supports_tool_calls is False:
            note += " This child model has no native tool-call capability; do not claim that workspace tools executed."
        if isinstance(prompt, str) or prompt is None:
            return (prompt or "") + note
        return (*prompt, SystemPromptBlock(text=note, cache=False))

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

    def compaction(self, stream: StreamFn) -> CompactionConfig | None:
        """Opt in to the native policy only with executing-adapter metadata.

        Unknown custom adapters keep their old behavior. Hosts can instead pass
        an explicit CompactionConfig(auto=True, ...) to AgentConfig to require
        fail-closed validation. This never supplies an invented context window
        or output reserve and never sets a lifetime/call/token budget.
        """
        metadata = stream.resolve_model(self.id) if isinstance(stream, ModelStreamFn) else self.metadata
        if metadata is None or metadata.context_window <= 0 or not (self.options.max_tokens or metadata.max_tokens):
            return None
        budget = resolve_compaction_budget(metadata, self.options,
            context_window=self.context_window or None, max_prompt_tokens=self.max_prompt_tokens or None)
        return CompactionConfig(auto=True, context_window=budget.context_window,
                                max_prompt_tokens=budget.max_prompt_tokens or None,
                                output_reserve=budget.output_reserve)

    def stream(self, original: StreamFn) -> StreamFn:
        def guarded(model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
            # New children are fresh. Their own signed history belongs to this
            # model and must not be stripped merely because it differs from the
            # parent's model. history() remains an explicit cross-model codec.
            if self.metadata is not None and isinstance(original, ModelStreamFn):
                live = original.resolve_model(model)
                if live is None or live != self.metadata:
                    raise ValueError("Child model metadata changed/unavailable; no request or fallback")
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
                    # Use the SAME built-in text estimate as auto compaction.
                    # Comparing UTF-8 bytes to tokens vetoed long tool loops
                    # before the native 80% trigger could ever compact them.
                    # Inline base64 is transport, not image model text; retain
                    # the existing conservative per-image admission allowance.
                    def without_image_transport(message: Message) -> Message:
                        if isinstance(message, UserMessage) and not isinstance(message.content, str):
                            return replace(message, content=tuple(
                                replace(part, data="") if isinstance(part, ImageContent) else part
                                for part in message.content))
                        if isinstance(message, ToolResultMessage):
                            return replace(message, content=tuple(
                                replace(part, data="") if isinstance(part, ImageContent) else part
                                for part in message.content))
                        return message
                    projected = tuple(without_image_transport(message) for message in context.messages)
                    size = estimate_input_tokens(replace(context, messages=projected), options) + 2048 * len(images)
                    limit = min(self.max_prompt_tokens or self.context_window,
                                self.context_window - (options.max_tokens or 0))
                    if limit <= 0 or size > limit:
                        raise ValueError("Child context too large; compact history or choose a larger model")
            return original(model, context, options)
        return preserve_model_metadata(guarded, original)


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
