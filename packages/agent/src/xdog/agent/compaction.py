"""One built-in Coding compaction policy, with safe SDK request boundaries.

This module never mutates an Agent, enables auto mode, persists history, or runs
business tools. Plan at an idle/completed-batch boundary; await a candidate; then
commit its messages and context together only if the caller still owns that
boundary. Exceptions (including cancellation) leave the caller's history alone.

Token counts are deliberately labelled estimates, not tokenizer guarantees.
"""
from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, cast

from xdog.agent.core import AgentMessage, CustomAgentMessage, StreamFn
from xdog.agent.summarization import stream_summary, summary_instructions
from xdog.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    ImageContent,
    Message,
    Model,
    StreamOptions,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
    system_prompt_text,
)
from xdog.ai.utils.event_stream import EventStream

TRIGGER_RATIO = 0.80
TARGET_RATIO = 0.50
SUMMARY_TEMPLATE = """\
<conversation_summary>
The conversation so far has been summarized. Here is the summary:

{summary}
</conversation_summary>
"""

SUMMARIZE_SYSTEM_PROMPT = """\
You are a conversation summarizer. You will receive a portion of a conversation \
between a user and a coding assistant. Produce a concise summary that captures:

1. What tasks were discussed and their current status.
2. Key decisions made.
3. Important code changes or file paths mentioned.
4. Any outstanding issues or next steps.

Be concise but ensure no critical context is lost. Output only the summary text.
"""


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token."""
    return len(text) // 4


def estimate_message_tokens(msg: AgentMessage) -> int:
    """Legacy Coding estimate; SDK request budgeting uses estimate_input_tokens."""
    total = 0

    if isinstance(msg, UserMessage):
        if isinstance(msg.content, str):
            total += estimate_tokens(msg.content)
        else:
            for part in msg.content:
                if isinstance(part, TextContent):
                    total += estimate_tokens(part.text)
                else:
                    total += 200
    elif isinstance(msg, AssistantMessage):
        for reply_part in msg.content:
            if isinstance(reply_part, TextContent):
                total += estimate_tokens(reply_part.text)
            else:
                total += 200
    elif isinstance(msg, ToolResultMessage):
        for part in msg.content:
            if isinstance(part, TextContent):
                total += estimate_tokens(part.text)
            else:
                total += 200
    else:
        total += 200

    # Overhead for role, formatting
    total += 10
    return total


def estimate_history_tokens(messages: list[AgentMessage]) -> int:
    """Estimate total tokens across all messages."""
    return sum(estimate_message_tokens(m) for m in messages)


def split_messages(
    messages: list[AgentMessage],
    target_recent_tokens: int,
) -> tuple[list[AgentMessage], list[AgentMessage]]:
    """Split messages into (old, recent) where recent fits within target tokens.

    Walks backwards from the end, accumulating messages into the "recent"
    bucket until the target is reached.
    """
    cut = recent_start([estimate_message_tokens(message) for message in messages], target_recent_tokens)
    return messages[:cut], messages[cut:]


def recent_start(token_counts: Sequence[int], target_tokens: int) -> int:
    """Coding's backwards recent-window selection (always keep the last message)."""
    accumulated = 0
    cut = len(token_counts)
    for count in reversed(token_counts):
        if accumulated + count > target_tokens and cut < len(token_counts):
            break
        accumulated += count
        cut -= 1
    return cut


def extract_text(msg: AgentMessage) -> str:
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    return "\n".join(part.text for part in content or () if isinstance(part, TextContent))


def conversation_text(messages: Sequence[AgentMessage]) -> str:
    """Coding's summary projection: visible content, calls, results, no opaque metadata."""
    parts: list[str] = []
    for msg in messages:
        text = extract_text(msg)
        if isinstance(msg, UserMessage):
            if text:
                parts.append(f"[USER]: {text}")
        elif isinstance(msg, AssistantMessage):
            calls = [f"[TOOL CALL {part.name}]: {part.arguments}" for part in msg.content
                     if isinstance(part, ToolCall)]
            if text or calls:
                parts.append(f"[ASSISTANT]: {text}" + "\n" + "\n".join(calls))
        elif isinstance(msg, ToolResultMessage):
            parts.append(f"[TOOL RESULT {msg.tool_name}, error={msg.is_error}]: {text}")
        else:
            from xdog.agent.extensions.context_message import summary_text
            if custom_text := summary_text(msg):
                parts.append("[EXTENSION CONTEXT]: " + custom_text)
    return "\n\n".join(parts)


def summary_context(text: str, instructions: str = "") -> Context:
    instructions = summary_instructions(instructions)
    if instructions:
        text += "\n\nUser's compaction focus (not a request to execute tools):\n" + instructions
    return Context(system_prompt=SUMMARIZE_SYSTEM_PROMPT, messages=(UserMessage(content=text),), tools=())


class CompactionError(ValueError):
    """No candidate was committed. Usage can include a rejected summary request."""

    def __init__(self, message: str, *, usage: Usage = Usage(), cleanup_failed: bool = False) -> None:
        super().__init__(message + "; original history retained")
        self.usage = usage
        self.cleanup_failed = cleanup_failed


@dataclass(frozen=True)
class CompactionConfig:
    """Opt-in automatic compaction; manual calls use the same built-in policy.

    Capacity overrides are trusted host data, never restored from a snapshot.
    Unknown adapters require explicit capacity and output reserve.
    """

    auto: bool = False
    context_window: int | None = None
    max_prompt_tokens: int | None = None
    output_reserve: int | None = None
    safety_margin: int = 1024
    instructions: str = ""

    def __post_init__(self) -> None:
        if type(self.auto) is not bool:
            raise ValueError("Compaction auto must be a boolean")
        summary_instructions(self.instructions)
        for name in ("context_window", "max_prompt_tokens", "output_reserve"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"Compaction {name} must be positive")
        if type(self.safety_margin) is not int or self.safety_margin < 0:
            raise ValueError("Compaction safety_margin must be nonnegative")


@dataclass(frozen=True)
class CompactionBudget:
    """Trusted limits, never inferred from AgentConfig's historical default.

    ``max_prompt_tokens`` is the model's separate input ceiling (zero = absent).
    The output reserve is also the explicit max_tokens of the summary request.
    """

    context_window: int
    output_reserve: int
    max_prompt_tokens: int = 0
    safety_margin: int = 1024

    def __post_init__(self) -> None:
        for name in ("context_window", "output_reserve", "max_prompt_tokens", "safety_margin"):
            value = getattr(self, name)
            if type(value) is not int or value < (1 if name in {"context_window", "output_reserve"} else 0):
                raise ValueError(f"Invalid compaction {name}")
        if self.input_limit <= 0:
            raise CompactionError("No input capacity remains after reserves")

    @property
    def input_limit(self) -> int:
        return min(self.max_prompt_tokens or self.context_window,
                   self.context_window - self.output_reserve) - self.safety_margin


def resolve_compaction_budget(
    model: Model | None = None, options: StreamOptions = StreamOptions(), *,
    context_window: int | None = None, max_prompt_tokens: int | None = None,
    output_reserve: int | None = None, safety_margin: int = 1024,
) -> CompactionBudget:
    """Resolve cached metadata from the *executing* adapter, or explicit host limits.

    No provider lookup/network or 200k fallback. Host limits may narrow known
    model limits, not exceed them. Unknown capacity/output reserve fails closed.
    """
    for name, value in (("context_window", context_window), ("max_prompt_tokens", max_prompt_tokens),
                        ("output_reserve", output_reserve)):
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError(f"Compaction {name} must be positive")
    windows = [value for value in (context_window, model.context_window if model else None) if value]
    if not windows:
        raise CompactionError("Unknown model context capacity; supply trusted capacity explicitly")
    inputs = [value for value in (max_prompt_tokens, model.max_prompt_tokens if model else None) if value]
    output = output_reserve or options.max_tokens or (model.max_tokens if model else 0)
    if not output:
        raise CompactionError("Unknown model output reserve; supply it explicitly")
    if options.max_tokens is not None and output < options.max_tokens:
        raise CompactionError("Output reserve is smaller than the actual requested output")
    if model and model.max_tokens and output > model.max_tokens:
        raise CompactionError("Output reserve exceeds the selected model output limit")
    return CompactionBudget(min(windows), output, min(inputs) if inputs else 0, safety_margin)


def _text_tokens(text: str) -> int:
    # UTF-8 bytes avoids the especially severe chars/4 undercount for CJK text.
    return (len(text.encode("utf-8")) + 3) // 4


def _json_tokens(value: Any) -> int:
    return _text_tokens(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


def estimate_input_message_tokens(message: Message) -> int:
    """Request estimate including arguments/thinking/signatures, not usage or details.

    Inline image bytes are a conservative size proxy, not a vision tokenizer.
    Neither this nor the safety margin can promise an exact provider token count.
    """
    total = 10
    if isinstance(message.content, str):
        return total + _text_tokens(message.content)
    for part in message.content:
        if isinstance(part, TextContent):
            total += _text_tokens(part.text) + _text_tokens(part.text_signature or "")
        elif isinstance(part, ThinkingContent):
            total += _text_tokens(part.thinking) + _text_tokens(part.thinking_signature or "")
        elif isinstance(part, ToolCall):
            total += _text_tokens(part.id + part.name + (part.thought_signature or ""))
            total += _json_tokens(part.arguments) + 10
        elif isinstance(part, ImageContent):
            total += max(200, _text_tokens(part.data)) + _text_tokens(part.mime_type)
    if isinstance(message, ToolResultMessage):
        total += _text_tokens(message.tool_call_id + message.tool_name) + 4
    return total


def estimate_input_tokens(context: Context, options: StreamOptions = StreamOptions()) -> int:
    """Estimate the FULL prepared request, including system/session skills and tools.

    Turn skills and other request-only messages must already be in context.messages.
    """
    total = 16 + _text_tokens(system_prompt_text(context.system_prompt) or "")
    if options.response_format is not None:
        schema = options.response_format
        total += 16 + _text_tokens(schema.schema_json + schema.name + (schema.description or ""))
    if options.tool_choice is not None:
        total += 4 + _text_tokens(options.tool_choice.type + (options.tool_choice.name or ""))
    if isinstance(context.system_prompt, tuple):
        total += 4 * len(context.system_prompt)
    for tool in context.tools or ():
        total += 16 + _text_tokens(tool.name) + _text_tokens(tool.description) + _json_tokens(tool.parameters)
    return total + sum(estimate_input_message_tokens(message) for message in context.messages)


def safe_cut_indices(messages: Sequence[AgentMessage]) -> tuple[int, ...]:
    """Validate complete consecutive batches, returning boundaries *after* each.

    Unlike navigation's permissive history_points, reject orphan/duplicate results,
    reused call IDs, interleaved messages and unfinished batches before compaction.
    """
    pending: dict[str, str] = {}
    seen: set[str] = set()
    cuts = [0]
    for index, message in enumerate(messages):
        if isinstance(message, ToolResultMessage):
            if pending.get(message.tool_call_id) != message.tool_name:
                raise CompactionError("Orphan, duplicate or mismatched tool result")
            del pending[message.tool_call_id]
        else:
            if pending:
                raise CompactionError("Cannot compact an incomplete tool batch")
            if isinstance(message, AssistantMessage):
                for part in message.content:
                    if isinstance(part, ToolCall):
                        if not part.id or part.id in seen:
                            raise CompactionError("Duplicate or empty tool call ID")
                        seen.add(part.id)
                        pending[part.id] = part.name
        if not pending:
            cuts.append(index + 1)
    if pending:
        raise CompactionError("Cannot compact an incomplete tool batch")
    return tuple(cuts)


@dataclass(frozen=True)
class CompactionPlan:
    """Owned snapshot. ``removed_indices`` addresses canonical original history.

    ``first_kept_index`` is the recent suffix boundary; protected/task messages
    before it survive too. Projection/prefix are request-only, not persisted as
    additional user turns. Do not mutate nested payloads in this snapshot.
    """

    original_messages: tuple[AgentMessage, ...]
    context: Context
    projections: tuple[tuple[Message, ...], ...]
    prefix_messages: tuple[Message, ...]
    kept_indices: tuple[int, ...]
    removed_indices: tuple[int, ...]
    first_kept_index: int
    budget: CompactionBudget
    estimated_input_before: int
    extra_input_tokens: int
    target_recent_tokens: int
    generation: int
    force: bool


def plan_compaction(
    messages: Sequence[AgentMessage], *, context: Context, budget: CompactionBudget,
    force: bool = False, generation: int = 0, protected_indices: Sequence[int] = (),
    projections: Sequence[Sequence[Message]] | None = None,
    prefix_messages: Sequence[Message] = (), options: StreamOptions = StreamOptions(),
) -> CompactionPlan | None:
    """Pure Coding 80%/50% policy; None means below the trigger, not auto-enabled.

    Pass the ACTUAL prepared Context. For custom/conversion hooks supply one
    projection per canonical message; prefix_messages carries request-only skills.
    Their concatenation must exactly equal context.messages (no guessed mapping).
    Explicitly protect any history-carried active skills via protected_indices.
    The task's user message is automatically retained for an intra-turn cut.
    """
    if options.max_tokens is not None and options.max_tokens > budget.output_reserve:
        raise CompactionError("Output reserve is smaller than the actual requested output")
    if type(generation) is not int or generation < 0:
        raise ValueError("Compaction generation must be a nonnegative integer")
    if projections is None:
        if any(isinstance(message, CustomAgentMessage) for message in messages):
            raise CompactionError("Custom history requires explicit request projections")
        projections = [(cast(Message, message),) for message in messages]
    projected = tuple(tuple(group) for group in projections)
    if len(projected) != len(messages):
        raise CompactionError("Request projections must align with canonical history")
    prefix = tuple(prefix_messages)
    if prefix + tuple(msg for group in projected for msg in group) != context.messages:
        raise CompactionError("Actual request does not match canonical history projections")
    safe = set(safe_cut_indices(messages))
    # Validate projected tool pairing too: custom conversion must not hide half a batch.
    projected_safe = set(safe_cut_indices(context.messages))
    offset = len(prefix)
    aligned_safe = []
    for index, group in enumerate(projected):
        if index in safe and offset in projected_safe:
            aligned_safe.append(index)
        offset += len(group)
    before = estimate_input_tokens(context, options)
    extra = before - estimate_input_tokens(context)
    if not force and before < budget.input_limit * TRIGGER_RATIO:
        return None
    overhead = estimate_input_tokens(replace(context, messages=prefix), options)
    if overhead >= budget.input_limit:
        raise CompactionError("Required system/tools/skills exhaust input capacity")
    counts = [sum(estimate_input_message_tokens(msg) for msg in group) for group in projected]
    target = max(1, min(int(budget.input_limit * TARGET_RATIO), budget.input_limit - overhead))
    if force:
        target = min(target, max(1, sum(counts) // 2))
    wanted = recent_start(counts, target)
    cut = max((index for index in aligned_safe if index <= wanted), default=0)
    protected = set(protected_indices)
    for index in protected:
        if type(index) is not int or not 0 <= index < len(messages):
            raise ValueError("Invalid protected history index")
        if any(not isinstance(msg, UserMessage) for msg in projected[index]):
            raise CompactionError("Only user/skill context can be individually protected")
    if cut and not any(isinstance(message, UserMessage) for message in messages[cut:]):
        task_index = max((index for index in range(cut) if isinstance(messages[index], UserMessage)), default=-1)
        if task_index >= 0:
            protected.add(task_index)
    kept = tuple(index for index in range(len(messages)) if index >= cut or index in protected)
    removed = tuple(index for index in range(cut) if index not in protected)
    if not removed:
        raise CompactionError("No safe boundary makes compaction progress")
    # Fail before spending a summary request if even an empty wrapper cannot fit.
    minimum = (UserMessage(content=SUMMARY_TEMPLATE.format(summary="")),)
    kept_projection = tuple(msg for index in kept for msg in projected[index])
    candidate = replace(context, messages=prefix + minimum + kept_projection)
    if estimate_input_tokens(candidate, options) >= min(before, budget.input_limit):
        raise CompactionError("Required recent history/task/skills leave no room for a useful summary")
    return deepcopy(CompactionPlan(tuple(messages), context, projected, prefix, kept, removed, cut, budget,
                                   before, extra, target, generation + 1, force))


@dataclass(frozen=True)
class CompactionMetadata:
    generation: int
    first_kept_index: int
    original_count: int
    removed_indices: tuple[int, ...]
    kept_indices: tuple[int, ...]
    estimated_input_before: int
    estimated_input_after: int
    input_limit: int
    target_recent_tokens: int
    summary_index: int = 0
    source: str = "builtin-coding"
    version: int = 1


@dataclass(frozen=True)
class CompactionResult:
    summary: str
    messages: tuple[AgentMessage, ...]
    removed_messages: tuple[AgentMessage, ...]
    metadata: CompactionMetadata
    context: Context
    usage: Usage


class _UsageStream(EventStream[AssistantMessage]):
    """Observe the final summary usage without replacing shared validation/cleanup."""

    def __init__(self, source: EventStream[AssistantMessage], usage: list[Usage]) -> None:
        self.source = source
        self.usage = usage

    def __aiter__(self) -> AsyncIterator[AssistantMessageEvent]:
        return self.source.__aiter__()

    async def aclose(self) -> None:
        close = getattr(self.source, "aclose", None)
        if callable(close):
            await close()

    async def result(self) -> AssistantMessage:
        result = await self.source.result()
        self.usage[0] = result.usage
        return result


async def summarize_compaction(
    plan: CompactionPlan, stream_fn: StreamFn, model: str, *,
    options: StreamOptions = StreamOptions(), instructions: str = "",
) -> CompactionResult:
    """Generate and validate one candidate using the current adapter, with no tools.

    Callers must reject concurrent manual compaction and stale plans themselves.
    No retry loop: oversized summary requests or non-shrinking/oversized candidates
    fail explicitly. Success usage is separate; CompactionError carries failure usage
    when the adapter supplied it. Cancellation propagates without committing state.
    """
    plan = deepcopy(plan)
    # Include protected task/skill messages in the summary source too, so the
    # summarizer sees the goal even though the original goal survives verbatim.
    old = tuple(msg for group in plan.projections[:plan.first_kept_index] for msg in group)
    request = summary_context(conversation_text(old), instructions)
    if estimate_input_tokens(request) > plan.budget.input_limit:
        raise CompactionError("Summary request exceeds model input capacity")
    usage = [Usage()]
    streams: list[_UsageStream] = []

    async def observe(name: str, context: Context, selected: StreamOptions) -> EventStream[AssistantMessage]:
        response = stream_fn(name, context, selected)
        if inspect.isawaitable(response):
            response = await response
        wrapped = _UsageStream(response, usage)
        streams.append(wrapped)
        return wrapped

    try:
        try:
            summary = await stream_summary(cast(StreamFn, observe), model, request,
                                           replace(options, max_tokens=plan.budget.output_reserve))
        finally:
            # stream_summary closes its iterator; release the response as well.
            # This owns only this request, never the caller's shared adapter.
            for stream in streams:
                try:
                    await stream.aclose()
                except Exception as exc:
                    raise CompactionError("Compaction stream cleanup failed", usage=usage[0],
                                          cleanup_failed=True) from exc
        summary_message = UserMessage(content=SUMMARY_TEMPLATE.format(summary=summary))
        kept = tuple(plan.original_messages[index] for index in plan.kept_indices)
        messages = (summary_message,) + kept
        projected = tuple(msg for index in plan.kept_indices for msg in plan.projections[index])
        context = replace(plan.context, messages=plan.prefix_messages + (summary_message,) + projected)
        safe_cut_indices(messages)
        safe_cut_indices(context.messages)
        after = estimate_input_tokens(context) + plan.extra_input_tokens
        if after > plan.budget.input_limit or after >= plan.estimated_input_before:
            raise CompactionError("Summary candidate exceeds capacity or makes no progress")
    except CompactionError as exc:
        exc.usage = usage[0]
        raise
    except ValueError as exc:
        raise CompactionError("Compaction summary validation failed", usage=usage[0]) from exc
    metadata = CompactionMetadata(plan.generation, plan.first_kept_index, len(plan.original_messages),
                                  plan.removed_indices, plan.kept_indices, plan.estimated_input_before,
                                  after, plan.budget.input_limit, plan.target_recent_tokens)
    return CompactionResult(summary, messages, tuple(plan.original_messages[index] for index in plan.removed_indices),
                            metadata, context, usage[0])
