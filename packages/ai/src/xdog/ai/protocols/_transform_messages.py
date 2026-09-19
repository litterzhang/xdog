"""Message transformation for the OpenAI Chat Completions format.

Converts :class:`~ai.types.Context` and :class:`~ai.types.Message` types
to OpenAI-compatible dicts.

Faithfully ports the TypeScript ``transform-messages.ts`` and the
``convertMessages`` / ``convertTools`` helpers from
``openai-completions.ts``.

Key behaviours:
- **Orphan tool call detection**: if an assistant message has tool calls
  but the following messages lack matching tool results, synthetic
  ``"No result provided"`` tool results are inserted.
- **Error / abort skip**: assistant messages with ``stop_reason`` of
  ``"error"`` or ``"aborted"`` are dropped (they are incomplete turns).
- **Thinking block conversion**: for cross-model replay, thinking blocks
  are converted to plain text; for same-model they are preserved as-is.
- **Tool call ID normalisation**: a caller-supplied callback can rewrite
  IDs for cross-provider compatibility.
- **Unicode sanitisation**: all user-facing text is run through
  :func:`~ai.utils.sanitize_unicode.sanitize_unicode`.

Only the OpenAI Chat Completions format is retained (used by Copilot and
any OpenAI-compatible API).  Anthropic and Google converters have been
removed.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from xdog.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Message,
    Model,
    OpenAICompletionsCompat,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from xdog.ai.utils.sanitize_unicode import sanitize_unicode

# Type alias for the optional tool-call-ID normaliser callback.
# Signature: (original_id, model, assistant_msg) -> normalised_id
NormalizeToolCallId = Callable[[str, Model, AssistantMessage], str]


# ---------------------------------------------------------------------------
# Low-level message transforms  (matches TS ``transformMessages``)
# ---------------------------------------------------------------------------


def transform_messages(
    messages: tuple[Message, ...] | list[Message],
    model: Model,
    normalize_tool_call_id: NormalizeToolCallId | None = None,
) -> list[Message]:
    """Two-pass message transform matching the TS ``transformMessages``.

    **Pass 1** — per-message content transforms:
    - Thinking blocks: drop redacted cross-model, convert non-same-model
      thinking to text, strip empty thinking, preserve same-model sigs.
    - Tool call IDs: normalise via callback & build an id-map.
    - Tool result IDs: rewrite from the id-map.

    **Pass 2** — structural fixes:
    - Skip ``error`` / ``aborted`` assistant messages.
    - Insert synthetic tool results for orphaned tool calls.
    """
    tool_call_id_map: dict[str, str] = {}

    # ---- Pass 1: content-level transforms ----
    transformed: list[Message] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            transformed.append(msg)
            continue

        if isinstance(msg, ToolResultMessage):
            mapped_id = tool_call_id_map.get(msg.tool_call_id)
            if mapped_id and mapped_id != msg.tool_call_id:
                transformed.append(replace(msg, tool_call_id=mapped_id))
            else:
                transformed.append(msg)
            continue

        if isinstance(msg, AssistantMessage):
            is_same_model = (
                msg.provider == model.provider
                and msg.api == model.api
                and msg.model == model.id
            )

            new_content: list[Any] = []
            for block in msg.content:
                if isinstance(block, ThinkingContent):
                    # Redacted thinking — only valid for same model
                    if block.redacted:
                        if is_same_model:
                            new_content.append(block)
                        continue

                    # Same model with signature — keep for replay
                    if is_same_model and block.thinking_signature:
                        new_content.append(block)
                        continue

                    # Empty thinking — skip
                    if not block.thinking or not block.thinking.strip():
                        continue

                    # Same model — keep as thinking
                    if is_same_model:
                        new_content.append(block)
                        continue

                    # Cross-model — convert to text
                    new_content.append(TextContent(text=block.thinking))
                    continue

                if isinstance(block, TextContent):
                    if is_same_model:
                        new_content.append(block)
                    else:
                        # Strip signature for cross-model
                        new_content.append(TextContent(text=block.text))
                    continue

                if isinstance(block, ToolCall):
                    normalized_block = block

                    # Strip thought signature for cross-model
                    if not is_same_model and block.thought_signature:
                        normalized_block = replace(normalized_block, thought_signature=None)

                    # Normalise tool call ID for cross-model
                    if not is_same_model and normalize_tool_call_id is not None:
                        norm_id = normalize_tool_call_id(block.id, model, msg)
                        if norm_id != block.id:
                            tool_call_id_map[block.id] = norm_id
                            normalized_block = replace(normalized_block, id=norm_id)

                    new_content.append(normalized_block)
                    continue

                # Unknown block type — pass through
                new_content.append(block)

            transformed.append(replace(msg, content=tuple(new_content)))
            continue

        # Fallback — pass through
        transformed.append(msg)

    # ---- Pass 2: structural fixes ----
    result: list[Message] = []
    pending_tool_calls: list[ToolCall] = []
    existing_tool_result_ids: set[str] = set()

    def _flush_orphan_tool_calls() -> None:
        """Insert synthetic tool results for any unmatched pending tool calls."""
        for tc in pending_tool_calls:
            if tc.id not in existing_tool_result_ids:
                result.append(
                    ToolResultMessage(
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        content=(TextContent(text="No result provided"),),
                        is_error=True,
                        timestamp=time.time(),
                    )
                )

    for msg in transformed:
        if isinstance(msg, AssistantMessage):
            # Flush orphans from previous assistant
            if pending_tool_calls:
                _flush_orphan_tool_calls()
                pending_tool_calls = []
                existing_tool_result_ids = set()

            # Skip errored / aborted assistant messages
            if msg.stop_reason in ("error", "aborted"):
                continue

            # Track tool calls from this assistant
            tool_calls = [b for b in msg.content if isinstance(b, ToolCall)]
            if tool_calls:
                pending_tool_calls = tool_calls
                existing_tool_result_ids = set()

            result.append(msg)

        elif isinstance(msg, ToolResultMessage):
            existing_tool_result_ids.add(msg.tool_call_id)
            result.append(msg)

        elif isinstance(msg, UserMessage):
            # User message interrupts tool flow — flush orphans
            if pending_tool_calls:
                _flush_orphan_tool_calls()
                pending_tool_calls = []
                existing_tool_result_ids = set()
            result.append(msg)

        else:
            result.append(msg)

    return result


# ---------------------------------------------------------------------------
# Default tool-call-ID normaliser (matches TS ``normalizeToolCallId``)
# ---------------------------------------------------------------------------

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_-]")


def default_normalize_tool_call_id(
    tc_id: str,
    model: Model,
    _msg: AssistantMessage,
) -> str:
    """Normalise a tool-call ID for cross-provider compatibility.

    Truncates to 40 chars and strips any pipe-separated suffixes.
    """
    if "|" in tc_id:
        call_id = tc_id.split("|", 1)[0]
        return _SAFE_ID_RE.sub("_", call_id)[:40]
    return tc_id[:40] if len(tc_id) > 40 else tc_id


# ---------------------------------------------------------------------------
# High-level: Context → OpenAI Chat Completions body
# (matches TS ``convertMessages`` + ``buildParams`` partially)
# ---------------------------------------------------------------------------


def context_to_openai(
    context: Context,
    model: Model,
) -> dict[str, Any]:
    """Convert a :class:`Context` to the OpenAI Chat Completions request body.

    Reads :class:`OpenAICompletionsCompat` from ``model.compat`` to decide:
    - ``system`` vs ``developer`` role for the system prompt
    - ``requires_assistant_after_tool_result``
    - ``requires_tool_result_name``
    - ``requires_thinking_as_text``
    - ``supports_strict_mode``

    Also applies :func:`sanitize_unicode` to all user/system text and
    :func:`transform_messages` for structural normalisation.
    """
    compat = (
        model.compat
        if isinstance(model.compat, OpenAICompletionsCompat)
        else OpenAICompletionsCompat()
    )

    # Transform messages (orphan detection, error skip, thinking, etc.)
    transformed = transform_messages(
        context.messages,
        model,
        normalize_tool_call_id=default_normalize_tool_call_id,
    )

    messages: list[dict[str, Any]] = []

    # System prompt
    if context.system_prompt:
        from xdog.ai.types import system_prompt_text
        text = system_prompt_text(context.system_prompt)
        if text:
            use_developer = model.reasoning and compat.supports_developer_role
            role = "developer" if use_developer else "system"
            messages.append({"role": role, "content": sanitize_unicode(text)})

    last_role: str | None = None

    idx = 0
    while idx < len(transformed):
        msg = transformed[idx]

        # Bridge: some providers don't allow user after toolResult
        if (
            compat.requires_assistant_after_tool_result
            and last_role == "toolResult"
            and isinstance(msg, UserMessage)
        ):
            messages.append({
                "role": "assistant",
                "content": "I have processed the tool results.",
            })

        if isinstance(msg, UserMessage):
            openai_msg = _user_message_to_openai(msg, model)
            if openai_msg is not None:
                messages.append(openai_msg)
                last_role = "user"

        elif isinstance(msg, AssistantMessage):
            openai_msg = _assistant_message_to_openai(msg, compat)
            if openai_msg is not None:
                messages.append(openai_msg)
                last_role = "assistant"

        elif isinstance(msg, ToolResultMessage):
            # Coalesce consecutive tool results
            image_blocks: list[dict[str, Any]] = []
            j = idx
            while j < len(transformed) and isinstance(transformed[j], ToolResultMessage):
                tr: ToolResultMessage = transformed[j]  # type: ignore[assignment]
                text_parts = [
                    sanitize_unicode(p.text)
                    for p in tr.content
                    if isinstance(p, TextContent)
                ]
                text_result = "\n".join(text_parts)
                has_images = any(isinstance(p, ImageContent) for p in tr.content)

                tool_msg: dict[str, Any] = {
                    "role": "tool",
                    "tool_call_id": tr.tool_call_id,
                    "content": sanitize_unicode(text_result) if text_result else "(see attached image)",
                }
                if compat.requires_tool_result_name and tr.tool_name:
                    tool_msg["name"] = tr.tool_name

                messages.append(tool_msg)

                if has_images and "image" in (model.input or ()):
                    for part in tr.content:
                        if isinstance(part, ImageContent):
                            image_blocks.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{part.mime_type};base64,{part.data}",
                                },
                            })
                j += 1

            idx = j

            if image_blocks:
                if compat.requires_assistant_after_tool_result:
                    messages.append({
                        "role": "assistant",
                        "content": "I have processed the tool results.",
                    })
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Attached image(s) from tool result:"},
                        *image_blocks,
                    ],
                })
                last_role = "user"
            else:
                last_role = "toolResult"
            continue  # idx already advanced

        idx += 1

    body: dict[str, Any] = {
        "model": model.id,
        "messages": messages,
    }

    if context.tools:
        body["tools"] = [_tool_to_openai(t, compat) for t in context.tools]
    elif _has_tool_history(context.messages):
        # Anthropic (via proxy) requires tools param when history has tool content
        body["tools"] = []

    return body


# ---------------------------------------------------------------------------
# Per-message converters
# ---------------------------------------------------------------------------


def _user_message_to_openai(
    msg: UserMessage,
    model: Model,
) -> dict[str, Any] | None:
    """Convert a :class:`UserMessage` to OpenAI format, or ``None`` to skip."""
    if isinstance(msg.content, str):
        return {"role": "user", "content": sanitize_unicode(msg.content)}

    parts: list[dict[str, Any]] = []
    for part in msg.content:
        if isinstance(part, TextContent):
            parts.append({"type": "text", "text": sanitize_unicode(part.text)})
        elif isinstance(part, ImageContent):
            parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{part.mime_type};base64,{part.data}",
                },
            })

    # Filter images if model doesn't support them
    if "image" not in (model.input or ()):
        parts = [p for p in parts if p.get("type") != "image_url"]

    if not parts:
        return None

    return {"role": "user", "content": parts}


def _assistant_message_to_openai(
    msg: AssistantMessage,
    compat: OpenAICompletionsCompat,
) -> dict[str, Any] | None:
    """Convert an :class:`AssistantMessage` to OpenAI format, or ``None`` to skip."""
    result: dict[str, Any] = {"role": "assistant"}

    # -- Text content --
    text_blocks = [b for b in msg.content if isinstance(b, TextContent)]
    non_empty_text = [b for b in text_blocks if b.text and b.text.strip()]

    if non_empty_text:
        # Always send as plain string (avoid recursive nesting with some providers)
        result["content"] = sanitize_unicode(
            "".join(b.text for b in non_empty_text)
        )
    else:
        result["content"] = "" if compat.requires_assistant_after_tool_result else None

    # -- Thinking blocks --
    thinking_blocks = [b for b in msg.content if isinstance(b, ThinkingContent)]
    non_empty_thinking = [b for b in thinking_blocks if b.thinking and b.thinking.strip()]

    if non_empty_thinking:
        if compat.requires_thinking_as_text:
            # Convert thinking to plain text
            thinking_text = "\n\n".join(b.thinking for b in non_empty_thinking)
            existing = result.get("content")
            if existing:
                result["content"] = thinking_text + "\n\n" + existing
            else:
                result["content"] = thinking_text
        else:
            # Use the signature from the first thinking block (for llama.cpp / gpt-oss)
            sig = non_empty_thinking[0].thinking_signature
            if sig and len(sig) > 0:
                result[sig] = "\n".join(b.thinking for b in non_empty_thinking)

    # -- Tool calls --
    tool_calls = [b for b in msg.content if isinstance(b, ToolCall)]
    if tool_calls:
        result["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                },
            }
            for tc in tool_calls
        ]

    # Skip empty assistant messages (no content, no tool calls)
    content = result.get("content")
    has_content = content is not None and (
        (isinstance(content, str) and len(content) > 0) or
        (isinstance(content, list) and len(content) > 0)
    )
    if not has_content and "tool_calls" not in result:
        return None

    return result


def _tool_to_openai(tool: Tool, compat: OpenAICompletionsCompat) -> dict[str, Any]:
    """Convert a :class:`Tool` to OpenAI function-calling format."""
    func: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
    }
    if compat.supports_strict_mode:
        func["strict"] = False

    return {
        "type": "function",
        "function": func,
    }


def _has_tool_history(messages: tuple[Message, ...]) -> bool:
    """Check if conversation contains tool calls or tool results."""
    for msg in messages:
        if isinstance(msg, ToolResultMessage):
            return True
        if isinstance(msg, AssistantMessage):
            if any(isinstance(b, ToolCall) for b in msg.content):
                return True
    return False
