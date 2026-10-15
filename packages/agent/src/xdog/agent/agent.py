"""High-level Agent class with state management, event subscriptions, and message queuing.

Usage::

    from xdog.agent import Agent
    from xdog.agent.helpers import stream_fn_from_provider
    import xdog.ai as ai
    agent = Agent(
        stream_fn_from_provider(ai.provider("copilot")),
        config=AgentConfig(model=model, system_prompt="You are helpful."),
        tools=[search_tool, code_tool],
    )

    event_stream = await agent.prompt("Hello!")
    async for event in event_stream:
        print(event)
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
import uuid
from collections.abc import Awaitable, Mapping, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xdog.agent.skills.manager import SkillManager
    from xdog.agent.skills.types import Skill
from dataclasses import replace
from typing import Any, Callable, TypeVar

from xdog.agent.agent_loop import run_agent_loop, run_agent_loop_continue
from xdog.agent.compaction import (
    SUMMARY_TEMPLATE,
    CompactionConfig,
    CompactionError,
    CompactionResult,
    plan_compaction,
    resolve_compaction_budget,
    summarize_compaction,
)
from xdog.agent.core import (
    AgentConfig,
    AgentContext,
    AgentMessage,
    AgentState,
    AgentTool,
    CustomAgentMessage,
    EmbedFn,
    ModelStreamFn,
    QueueMode,
    StreamFn,
    ToolExecutionMode,
    WebSearchFn,
)
from xdog.agent.event_stream import AgentEventStream
from xdog.agent.events import (
    AgentEndEvent,
    AgentEvent,
    CompactionEndEvent,
    CompactionFailedEvent,
    CompactionStartEvent,
    InputConsumedEvent,
    InputHandledEvent,
    InputRejectedEvent,
    InputTransformedEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    SnapshotEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
    UsageEvent,
)
from xdog.agent.input_queue import Delivery, QueuedInput
from xdog.agent.messages import dicts_to_messages
from xdog.agent.snapshots import (
    CompactionSnapshot,
    InterruptedTool,
    capture_skill_snapshots,
    decode_legacy_snapshot,
    decode_snapshot,
    encode_snapshot,
    validate_skill_bindings,
)
from xdog.agent.types import (
    AfterToolCallFn,
    AgentLoopConfig,
    BeforeToolCallFn,
    ConvertToLlmFn,
    HandledInput,
    InputTransformFn,
    TransformContextFn,
)
from xdog.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Message,
    StreamOptions,
    SystemPromptBlock,
    TextContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

logger = logging.getLogger(__name__)

# Tool/hook child tasks inherit the run owner, preventing self-await shutdown.
_running_agent: ContextVar[Agent | None] = ContextVar("xdog_running_agent", default=None)


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

_EventT = TypeVar("_EventT", bound=AgentEvent)

EventListener = Callable[[AgentEvent], None]
"""Synchronous callback invoked for every agent lifecycle event."""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

def _place_skills(
    manager: "SkillManager | None", active: "Sequence[str]"
) -> "tuple[str, str, list[Skill], dict[str, Skill]]":
    """Decide where each part of the skill story goes. The one place that does.

    Returns ``(index, fixed_bodies, transient, resolved)``:

    * **index** — one line per skill on disk. Small, and the same for every
      request, so it belongs at the very front of the system prompt where the
      prompt cache keeps it. It is also what makes the rest discoverable: an
      agent cannot ask for a skill it was never told exists.
    * **fixed bodies** — the active session-scoped skills. Also stable for the
      session, so also in the prefix, behind the index.
    * **transient** — active ``scope: turn`` skills, handed back for the caller
      to place as messages. They are going to be removed again, and adding then
      removing them from the prefix costs a full uncached re-send twice.

    Everything about *which* skills exist stays with the SkillManager the caller
    built, because only the caller knows where to look — flow beside its
    workflow, coding and claw in their group and shared directories.
    """
    if manager is None:
        return "", "", [], {}
    index = ""
    try:
        summary = manager.skills_summary()
        index = summary + "\n\n" if summary else ""
    except Exception:
        logger.debug("could not build the skill index", exc_info=True)
    resolved: list[Skill] = []
    for slug in active:
        try:
            skill = manager.load_skill(slug)
        except Exception:
            logger.debug("could not load skill %r", slug, exc_info=True)
            continue
        if skill is not None:
            resolved.append(skill)
    fixed = _skill_preamble([sk for sk in resolved if not sk.expires_after_turn])
    return index, fixed, [sk for sk in resolved if sk.expires_after_turn], {sk.slug: sk for sk in resolved}


def _skill_preamble(skills: "Sequence[Skill]") -> str:
    """Rendered bodies for skills that are fixed for the session.

    They do not change between requests, so they belong at the front of the
    system prompt where the prompt cache can keep them.
    """
    if not skills:
        return ""
    from xdog.agent.skills.render import render_skill_body

    return "\n\n".join(render_skill_body(sk) for sk in skills) + "\n\n"


def _transient_messages(skills: "Sequence[Skill]") -> "tuple[UserMessage, ...]":
    """Turn-scoped skills, as messages rather than as system-prompt text.

    They are going to be removed again, and the system prompt is the cached
    prefix: adding one there and taking it away costs a full re-send of
    everything behind it, twice. As messages they sit after the prefix, so the
    prefix stays cached and removing them is a message edit.
    """
    if not skills:
        return ()
    from xdog.agent.skills.render import render_skill_body

    return tuple(
        UserMessage(content=f"## Active skill: {sk.name}\n\n{render_skill_body(sk)}")
        for sk in skills
    )


class Agent:
    """High-level agent with state management, event subscriptions, and queuing.

    The Agent wraps the low-level :func:`agent_loop` with:

    * Persistent conversation state (:class:`AgentState`)
    * Subscriber-based event dispatching
    * Steering interrupts (:meth:`steer`) and follow-up injection
      (:meth:`follow_up`)
    * Cancellation via :meth:`abort`

    Parameters
    ----------
    stream_fn:
        Required. The function that calls the LLM. Build one from an ai
        Provider via ``stream_fn_from_provider(provider)``.
    config:
        Agent configuration (model, system_prompt, limits, capabilities,
        StreamOptions for LLM calls).
    tools:
        Tools available to the agent.
    embed_fn:
        If provided, a built-in ``embed`` tool is auto-registered.
    web_search_fn:
        If provided, a built-in ``web_search`` tool is auto-registered.
    convert_to_llm:
        Optional message converter.  Defaults to identity (plain Messages).
    transform_context:
        Optional async context transform (summarisation, RAG, etc.).
    before_tool_call:
        Async hook called before each tool execution; can block calls.
    after_tool_call:
        Async hook called after each tool execution; can override results.
    """

    def __init__(
        self,
        stream_fn: StreamFn,
        *,
        config: AgentConfig | None = None,
        tools: tuple[AgentTool, ...] | list[AgentTool] | None = None,
        tool_ctx: dict[str, Any] | None = None,
        skills: "SkillManager | None" = None,
        active_skills: "Sequence[str]" = (),
        embed_fn: EmbedFn | None = None,
        web_search_fn: WebSearchFn | None = None,
        convert_to_llm: ConvertToLlmFn | None = None,
        transform_context: TransformContextFn | None = None,
        before_tool_call: BeforeToolCallFn | None = None,
        after_tool_call: AfterToolCallFn | None = None,
        input_transform: InputTransformFn | None = None,
        input_settled: Callable[[tuple[QueuedInput, ...]], Awaitable[None]] | None = None,
    ) -> None:
        cfg = config or AgentConfig()
        if type(cfg.event_buffer_size) is not int or cfg.event_buffer_size <= 0:
            raise ValueError("event_buffer_size must be a positive integer")
        self._event_buffer_size = cfg.event_buffer_size
        if type(cfg.emit_snapshots) is not bool:
            raise ValueError("emit_snapshots must be a boolean")
        self._emit_snapshots = cfg.emit_snapshots
        self._session_id = uuid.uuid4().hex
        self._run_id = ""
        self._event_sequence = 0
        self._end_emitted = False
        self._input_transform = input_transform
        self._input_settled = input_settled
        self._input_checkpoint: Callable[[AgentEvent], Awaitable[None]] | None = None
        self._custom_queue: dict[int, tuple[Callable[[], bool], Callable[[], Awaitable[None]],
                                           Callable[[], Awaitable[None]], Callable[[], None]]] = {}
        self._input_rejected = False
        self._rejected_messages: list[AgentMessage] = []
        self._handled_inputs: list[InputHandledEvent] = []

        # Where a skill's instructions go is decided here, not by each caller.
        #
        # It used to be the caller's problem: the skills module handed back a
        # string and flow, coding and claw each chose where to concatenate it.
        # They chose differently, and one of them re-sent the whole conversation
        # uncached on every turn -- because prompt caching keys on the prefix,
        # and the system prompt is the front of it.
        #
        # The information needed to decide has always been on the skill itself.
        # One that never expires is fixed for the session, so it belongs in the
        # cacheable prefix. One that expires after a turn is going to move, and
        # editing the prefix to add or remove it invalidates everything behind
        # it -- so it goes after, where messages accumulate.
        #
        # Resolution stays with the caller, which is the real seam: flow looks
        # beside its workflow and in installed packages, coding looks in its
        # group and shared directories. Placement is the same everywhere.
        _base_prompt = cfg.system_prompt
        _index, _fixed_preamble, _transient, _resolved = _place_skills(skills, active_skills)
        _front = _index + _fixed_preamble
        if _front:
            prompt = ((SystemPromptBlock(text=_front, cache=True), *_base_prompt)
                      if isinstance(_base_prompt, tuple) else _front + (_base_prompt or ""))
            cfg = replace(cfg, system_prompt=prompt)


        # Build tools list — start with user-provided tools
        tools_list = list(tools) if tools else []

        # Auto-create tools from injected functions
        if web_search_fn is not None:
            from xdog.agent.tools import create_web_search_tool_from_fn
            tools_list.append(create_web_search_tool_from_fn(web_search_fn))

        if embed_fn is not None:
            from xdog.agent.tools import create_embed_tool_from_fn
            tools_list.append(create_embed_tool_from_fn(embed_fn))

        # The caller's prompt and the skill preamble are kept apart so either can
        # be replaced without the other being lost. coding rewrites its whole
        # system prompt before every turn; without this its skills would vanish
        # on the first rebuild, or it would have to re-render them itself, which
        # is the duplication this exists to remove.
        # None means "not determined" and behaves like True, which is what
        # every caller got before this existed.
        self._supports_tool_calls = cfg.supports_tool_calls
        self._context_window = cfg.context_window
        self._max_prompt_tokens = cfg.max_prompt_tokens
        self._base_system_prompt = _base_prompt
        self._skills = skills
        self._index = _index
        self._fixed_preamble = _fixed_preamble
        self._active_skill_slugs = tuple(dict.fromkeys(active_skills))
        self._turn_skills = tuple(_transient)
        self._resolved_skills = _resolved
        self._compaction_state = CompactionSnapshot()
        self._compaction_config = cfg.compaction or CompactionConfig()
        self._compaction_task: asyncio.Task[CompactionResult | None] | None = None
        self._compacting = False
        self._active_turn_slugs: tuple[str, ...] = ()
        self._resume_turn_skills: tuple[Skill, ...] = ()
        self._restored_run_active = False
        self._unconfirmed_tools: dict[str, str] = {}
        self._state = AgentState(
            system_prompt=cfg.system_prompt,
            model=cfg.model,
            tools=tuple(tools_list),
            messages=(),
        )

        self._active_tool_names: frozenset[str] | None = None
        self._tool_selection_revision = 0
        self._stream_fn = stream_fn
        self._config = cfg
        self._options = cfg.options
        self._tool_ctx = tool_ctx or {}
        self._convert_to_llm = convert_to_llm
        self._transform_context = transform_context
        self._tool_execution: ToolExecutionMode = cfg.tool_execution
        self._before_tool_call = before_tool_call
        self._after_tool_call = after_tool_call
        self._steering_mode = cfg.steering_mode
        self._follow_up_mode = cfg.follow_up_mode

        # Cancellation. The TUI may abort from another thread; the event itself
        # is always set on its owning loop and guarded by a run token.
        self._cancel = asyncio.Event()
        self._cancel_lock = threading.RLock()
        self._cancel_loop: asyncio.AbstractEventLoop | None = None
        self._cancel_token = 0
        self._pending_abort = False

        # Message queues
        self._steering_queue: list[AgentMessage] = []
        self._follow_up_queue: list[AgentMessage] = []
        self._identified_inputs: dict[str, tuple[QueuedInput, UserMessage]] = {}
        self._input_message_ids: dict[int, str] = {}

        # Event listeners
        self._listeners: list[EventListener] = []

        # Current event stream reference
        self._current_stream: AgentEventStream[AgentEvent] | None = None
        self._producer_task: asyncio.Task[None] | None = None
        self._producer_started = asyncio.Event()
        self._closing = False
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._stream_cleanup_errors: list[Exception] = []

        # Idle tracking (for wait_for_idle)
        self._idle_event = asyncio.Event()
        self._idle_event.set()  # starts idle

    # -- Public properties ---------------------------------------------------

    @property
    def session_id(self) -> str:
        """Data identity for the conversation; not an execution lease."""
        return self._session_id

    @property
    def run_id(self) -> str:
        """Most recent run identity, empty before the first run."""
        return self._run_id

    @property
    def closed(self) -> bool:
        """Whether owned runtime cleanup has completed, not merely been requested."""
        return self._closed

    def _ensure_open(self) -> None:
        if self._stream_cleanup_errors:
            raise RuntimeError("Agent stream cleanup is incomplete; cannot start new work")
        if self._closing or self._closed:
            raise RuntimeError("Agent is closing or closed")

    async def __aenter__(self) -> Agent:
        self._ensure_open()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Permanently stop this agent and await its owned run's cleanup.

        Call from the host, not a tool/hook running inside this agent (which
        would await itself). A cancelled caller does not cancel cleanup; another
        aclose() can await the same task. Shared provider adapters are not owned
        by the Agent and are never closed here. Uncooperative tools must be
        isolated by the host: this method cannot forcibly kill native code.
        """
        producer = self._producer_task
        if producer is not None and not producer.done():
            if producer.get_loop() is not asyncio.get_running_loop():
                raise RuntimeError("Agent close must run on its owning loop")
            if _running_agent.get() is self:
                raise RuntimeError("Cannot await Agent close from its own run; ask the host to close it")
        if self._closed:
            return
        if self._close_task is None:
            self._closing = True  # Close admission before yielding to any task.
            self.abort()
            self._close_task = asyncio.create_task(self._close_owned_runtime())
        await asyncio.shield(self._close_task)

    async def _close_owned_runtime(self) -> None:
        await self.cancel_and_wait()
        compact_task = self._compaction_task
        if compact_task is not None and not compact_task.done():
            if not compact_task.cancelling():
                compact_task.cancel()
            await asyncio.gather(compact_task, return_exceptions=True)
        if self._stream_cleanup_errors:
            raise RuntimeError("Agent stream cleanup is incomplete") from self._stream_cleanup_errors[0]
        self.clear_all_queues()
        self._listeners.clear()
        self._closed = True

    @property
    def state(self) -> AgentState:
        """Current agent state (read-only snapshot)."""
        return self._state

    @property
    def messages(self) -> tuple[AgentMessage, ...]:
        """Shortcut to the conversation message history."""
        return self._state.messages

    @property
    def is_streaming(self) -> bool:
        """Whether the agent is currently in a streaming loop."""
        return self._state.is_streaming

    @property
    def is_idle(self) -> bool:
        """A new run may start only after the producer and cleanup have settled."""
        return (self._idle_event.is_set() and not self._state.is_streaming
                and (self._producer_task is None or self._producer_task.done())
                and not self._closing and not self._closed and not self._stream_cleanup_errors)

    @property
    def cancellation_requested(self) -> bool:
        """Whether the current run has observed an abort request."""
        return self._cancel.is_set()

    @property
    def stream_fn(self) -> StreamFn:
        """The stream function used by this agent."""
        return self._stream_fn

    def set_stream_fn(self, stream_fn: StreamFn) -> None:
        """Replace the provider adapter while idle (for host-owned request limits)."""
        if self.state.is_streaming:
            raise RuntimeError("Cannot replace stream function during an active turn")
        self._stream_fn = stream_fn

    @property
    def tool_execution(self) -> ToolExecutionMode:
        """Get the current tool execution mode."""
        return self._tool_execution

    @property
    def context_window(self) -> int:
        """Host-configured context window; not an automatic compaction policy."""
        return self._context_window

    @property
    def max_prompt_tokens(self) -> int:
        """Host-configured prompt limit, zero when unspecified."""
        return self._max_prompt_tokens

    @property
    def supports_tool_calls(self) -> bool | None:
        """The host-selected tool calling capability, including explicit overrides."""
        return self._supports_tool_calls

    @property
    def options(self) -> StreamOptions:
        """The StreamOptions used for LLM calls."""
        return self._options

    def set_options(self, options: StreamOptions) -> None:
        """Replace the StreamOptions (temperature, thinking, max_tokens, etc.)."""
        self._options = options

    # -- State updates (immutable-style) ------------------------------------

    def _update_state(self, **kwargs: Any) -> None:
        """Replace the state with a new instance carrying the given overrides."""
        self._state = replace(self._state, **kwargs)

    # -- Public state mutators -----------------------------------------------

    def set_system_prompt(self, prompt: str | tuple[SystemPromptBlock, ...] | None) -> None:
        """Update the system prompt, keeping any skill preamble in front of it."""
        self._base_system_prompt = prompt
        self._recompose_system_prompt()

    @property
    def active_skills(self) -> tuple[str, ...]:
        """Host-selected skill slugs; used turn skills retire when their run settles."""
        return self._active_skill_slugs

    def set_active_skills(self, slugs: "Sequence[str]") -> None:
        """Replace idle skill selection; turn bodies belong to the next user prompt run.

        A run includes all its tool rounds and queued continuations. No model entry
        means no consumption. Skills never become persisted anonymous user history.
        """
        if self._state.is_streaming or not self._idle_event.is_set():
            raise RuntimeError("Cannot change active skills during an active run")
        selected = tuple(dict.fromkeys(slugs))
        self._index, self._fixed_preamble, transient, self._resolved_skills = _place_skills(self._skills, selected)
        self._active_skill_slugs = selected
        self._turn_skills = tuple(transient)
        self._recompose_system_prompt()

    def _recompose_system_prompt(self) -> None:
        base = self._base_system_prompt
        front = self._index + self._fixed_preamble
        if front:
            prompt = ((SystemPromptBlock(text=front, cache=True), *base)
                      if isinstance(base, tuple) else front + (base or ""))
            self._update_state(system_prompt=prompt)
        else:
            self._update_state(system_prompt=base)

    def set_model(self, model: str) -> None:
        """Update the model name."""
        self._update_state(model=model)

    def set_model_capabilities(self, *, context_window: int, max_prompt_tokens: int = 0,
                               supports_tool_calls: bool | None = None) -> None:
        """Host-selected cached metadata; never changes tool permissions or starts a model."""
        if self._state.is_streaming:
            raise RuntimeError("Cannot change model capabilities during an active turn")
        if (type(context_window) is not int or context_window < 0
                or type(max_prompt_tokens) is not int or max_prompt_tokens < 0
                or supports_tool_calls is not None and type(supports_tool_calls) is not bool):
            raise ValueError("Invalid model capability metadata")
        self._context_window = context_window
        self._max_prompt_tokens = max_prompt_tokens
        self._supports_tool_calls = supports_tool_calls

    def set_tools(self, tools: list[AgentTool] | tuple[AgentTool, ...]) -> None:
        """Replace configured tools; preserve any explicitly narrowed active selection.

        Removing a selected tool retires that name from the selection. A later
        registration with that name does not automatically restore the selection.
        """
        configured = tuple(tools)
        if self._active_tool_names is not None:
            names = [tool.name for tool in configured]
            if len(names) != len(set(names)):
                raise ValueError('Active tool selection requires unique configured names')
            self._active_tool_names = self._active_tool_names.intersection(names)
        if configured != self._state.tools:
            self._tool_selection_revision += 1
        self._update_state(tools=configured)

    @property
    def active_tools(self) -> tuple[AgentTool, ...]:
        """Configured-order selection used by both model definitions and execution.

        This is not tool permission: host preparation hooks still authorize effects.
        """
        selected = self._active_tool_names
        return (self._state.tools if selected is None else
                tuple(tool for tool in self._state.tools if tool.name in selected))

    @property
    def active_tool_selection(self) -> tuple[str, ...] | None:
        """Explicit selection in configured order; None means configured defaults.

        Hosts can capture this distinction without treating a narrowed selection
        as permission to adopt future registrations in a child or restored session.
        """
        return None if self._active_tool_names is None else tuple(tool.name for tool in self.active_tools)

    @property
    def tool_selection_revision(self) -> int:
        return self._tool_selection_revision

    def set_active_tools(self, names: list[str] | tuple[str, ...] | None, *, expected_revision: int) -> None:
        """Host-owned idle CAS selection, never a model/extension permission grant.

        None explicitly restores configured defaults, [] disables all tools. New
        registrations join defaults only; an explicit subset never grows implicitly.
        Hosts must add their own user review/session/child ownership and persistence.
        """
        selected = self.validate_active_tools(names, expected_revision=expected_revision)
        if selected != self._active_tool_names:
            self._active_tool_names = selected
            self._tool_selection_revision += 1

    def validate_active_tools(self, names: list[str] | tuple[str, ...] | None, *,
                              expected_revision: int) -> frozenset[str] | None:
        """Dry-run selection checks for hosts that must persist before publishing.

        This does not reserve ownership: hosts must keep their idle mutation owner
        through validation, durable commit and set_active_tools.
        """
        if self._state.is_streaming or self._producer_task is not None and not self._producer_task.done():
            raise RuntimeError('Active tool selection requires an idle agent')
        if type(expected_revision) is not int or expected_revision != self._tool_selection_revision:
            raise ValueError('Configured tools/selection changed; refresh before selecting')
        selected: frozenset[str] | None = None
        if names is not None:
            if (not isinstance(names, (tuple, list)) or len(names) > 2048
                    or any(not isinstance(name, str) or not name or len(name.encode()) > 256 for name in names)
                    or len(set(names)) != len(names)):
                raise ValueError('Active tools require bounded unique configured names')
            configured = [tool.name for tool in self._state.tools]
            if len(configured) != len(set(configured)) or not set(names) <= set(configured):
                raise ValueError('Active tool selection exceeds configured inventory')
            selected = frozenset(names)
        return selected

    @property
    def tool_context(self) -> dict[str, Any]:
        """An owned shallow copy of runtime references for idle context rebinding."""
        return dict(self._tool_ctx)

    def set_tool_context(self, context: Mapping[str, Any]) -> None:
        if self._state.is_streaming:
            raise RuntimeError("Cannot rebind tool context during an active run")
        self._tool_ctx = dict(context)

    def set_tool_execution(self, value: ToolExecutionMode) -> None:
        """Set tool execution mode."""
        self._tool_execution = value

    @property
    def context_transform(self) -> TransformContextFn | None:
        return self._transform_context

    def set_transform_context(self, value: TransformContextFn | None) -> None:
        """Rebind an owned pre-model transform only while the agent is idle."""
        if self._state.is_streaming:
            raise RuntimeError("Cannot rebind context transformation during an active run")
        self._transform_context = value

    def set_before_tool_call(self, value: BeforeToolCallFn | None) -> None:
        """Set the before_tool_call hook."""
        self._before_tool_call = value

    def drain_handled_inputs(self) -> list[InputHandledEvent]:
        events, self._handled_inputs = self._handled_inputs, []
        return events

    def drain_rejected_messages(self) -> list[AgentMessage]:
        """Recover refused plain queued input explicitly; it is never left in a live replay queue."""
        messages, self._rejected_messages = self._rejected_messages, []
        return messages

    @property
    def input_transform(self) -> InputTransformFn | None:
        return self._input_transform

    def set_input_transform(self, transform: InputTransformFn | None) -> None:
        if self._state.is_streaming:
            raise RuntimeError("Cannot replace input validation during an active run")
        self._input_transform = transform

    def set_input_settled(self, hook: Callable[[tuple[QueuedInput, ...]], Awaitable[None]] | None) -> None:
        """Await host persistence after identified input admission/handling, before model continuation.

        This is a durability hook, not a tool permission boundary. Failure stops
        continuation; already admitted history/handler effects are not rolled back.
        """
        if self._state.is_streaming:
            raise RuntimeError('Cannot replace input settlement during an active run')
        self._input_settled = hook

    def set_input_checkpoint(self, hook: Callable[[AgentEvent], Awaitable[None]] | None) -> None:
        """Persist prospective user history/handled feedback before publishing admission.

        A handled callback may already have effects; this is not an exactly-once
        tool/extension boundary. A failed checkpoint stops continuation.
        """
        if self._state.is_streaming:
            raise RuntimeError('Cannot replace input checkpoint during an active run')
        self._input_checkpoint = hook

    def set_after_tool_call(self, value: AfterToolCallFn | None) -> None:
        """Set the after_tool_call hook."""
        self._after_tool_call = value

    def set_steering_mode(self, mode: QueueMode) -> None:
        """Set the steering queue consumption mode."""
        self._steering_mode = mode

    def set_follow_up_mode(self, mode: QueueMode) -> None:
        """Set the follow-up queue consumption mode."""
        self._follow_up_mode = mode

    def replace_messages(self, messages: list[AgentMessage]) -> None:
        """Replace the entire message history."""
        self._update_state(messages=tuple(messages))
        self._unconfirmed_tools.clear()
        self._compaction_state = CompactionSnapshot()

    def append_message(self, message: AgentMessage) -> None:
        """Append a single message to the history."""
        self._update_state(messages=(*self._state.messages, message))

    def clear_messages(self) -> None:
        """Clear the message history."""
        self._update_state(messages=())
        self._unconfirmed_tools.clear()
        self._compaction_state = CompactionSnapshot()

    # -- Built-in compaction --------------------------------------------------

    async def compact(self, instructions: str = "") -> CompactionResult | None:
        """Manually apply Coding's built-in policy at an idle owned boundary.

        Does not enable auto mode. The returned removed history is data for the
        host to archive, not a persistence ACK. Shared provider clients stay open.
        """
        self._ensure_open()
        if not self._idle_event.is_set() or self._compacting:
            raise RuntimeError("Manual compaction requires an idle Agent")
        if self._unfinished_tools():
            raise RuntimeError("Unconfirmed tools require reconciliation before compaction")
        self._idle_event.clear()
        self._compacting = True

        async def perform() -> CompactionResult | None:
            try:
                messages = list(self._state.messages)
                if self._transform_context is not None:
                    messages = await self._transform_context(messages)
                converter = self._convert_to_llm or _default_convert_to_llm
                projected = converter(messages)
                if inspect.isawaitable(projected):
                    projected = await projected
                tools = self.active_tools if self._supports_tool_calls is not False else ()
                context = Context(system_prompt=self._state.system_prompt, messages=tuple(projected),
                                  tools=tuple(Tool(name=t.name, description=t.description, parameters=t.parameters)
                                              for t in tools))
                return await self._compact_context(context, automatic=False, instructions=instructions)
            finally:
                self._compacting = False
                self._idle_event.set()

        self._compaction_task = asyncio.create_task(perform())
        try:
            return await self._compaction_task
        finally:
            # If cancelled before the task's first instruction, its finally
            # never ran. Admission must still become idle, never remain wedged.
            if self._compaction_task.done():
                self._compacting = False
                self._idle_event.set()

    async def _observe(self, event: AgentEvent) -> None:
        event = self._emit(event)
        if self._current_stream is not None and not self._current_stream.closed:
            try:
                await self._current_stream.send(event)
            except asyncio.CancelledError:
                self._current_stream.send_cleanup(event)
                raise

    def _observe_cleanup(self, event: AgentEvent) -> None:
        event = self._emit(event)
        if self._current_stream is not None and not self._current_stream.closed:
            self._current_stream.send_cleanup(event)

    async def _compact_context(
        self, request: Context, *, automatic: bool,
        loop_context: AgentContext | None = None, prefix: tuple[UserMessage, ...] = (),
        instructions: str = "",
    ) -> CompactionResult | None:
        cfg = self._compaction_config
        original = self._state.messages
        if not original:
            return None
        options, model, adapter = self._options, self._state.model, self._stream_fn
        try:
            metadata = adapter.resolve_model(model) if isinstance(adapter, ModelStreamFn) else None
            budget = resolve_compaction_budget(metadata, options, context_window=cfg.context_window,
                                               max_prompt_tokens=cfg.max_prompt_tokens,
                                               output_reserve=cfg.output_reserve, safety_margin=cfg.safety_margin)
            # Arbitrary host transforms cannot safely be inverted. Accept only
            # an aligned projection; never guess which canonical data to drop.
            projections = tuple(tuple(_default_convert_to_llm([message])) for message in original)
            plan = plan_compaction(original, context=request, budget=budget, force=not automatic,
                                   generation=self._compaction_state.generation, projections=projections,
                                   prefix_messages=prefix, options=options)
            if plan is None:
                return None
            await self._observe(CompactionStartEvent(automatic=automatic))
            result = await summarize_compaction(plan, adapter, model, options=options,
                                               instructions=instructions or cfg.instructions)
            if self._closing or self._cancel.is_set() and automatic:
                raise asyncio.CancelledError
            if (self._state.messages != plan.original_messages or self._state.system_prompt != request.system_prompt
                    or self._state.model != model or self._stream_fn is not adapter or self._options != options):
                raise RuntimeError("Compaction source changed before commit; original history retained")
            # Commit both views without an intervening await. Product persistence
            # stays in its host adapter; this is only an in-memory transition.
            self._update_state(messages=result.messages)
            if loop_context is not None:
                loop_context.messages[:] = result.messages
            self._compaction_state = CompactionSnapshot(
                generation=result.metadata.generation, summary=result.summary,
                compacted_messages=self._compaction_state.compacted_messages + len(result.removed_messages))
        except asyncio.CancelledError:
            self._observe_cleanup(CompactionFailedEvent(error="Compaction cancelled; original history retained",
                                                       automatic=automatic))
            raise
        except Exception as exc:
            if isinstance(exc, CompactionError) and exc.cleanup_failed:
                self._stream_cleanup_errors.append(exc)
            await self._observe(CompactionFailedEvent(error=str(exc), automatic=automatic))
            if isinstance(exc, CompactionError) and (exc.usage.total_tokens or exc.usage.input or exc.usage.output):
                await self._observe(UsageEvent(usage=exc.usage, model=model, source="compaction"))
            raise
        ending = False
        try:
            await self._observe(UsageEvent(usage=result.usage, model=model, source="compaction"))
            ending = True
            await self._observe(CompactionEndEvent(summary=result.summary, removed_messages=result.removed_messages,
                                                 generation=result.metadata.generation, automatic=automatic))
        except asyncio.CancelledError:
            # A committed compaction is not a failed compaction. Keep its
            # original-history export available even when the consumer stalls.
            if not ending:
                self._observe_cleanup(CompactionEndEvent(summary=result.summary,
                    removed_messages=result.removed_messages, generation=result.metadata.generation,
                    automatic=automatic))
            raise
        return result

    # -- Session dump / restore ----------------------------------------------

    def _unfinished_tools(self) -> dict[str, str]:
        pending: dict[str, str] = {}
        for message in self._state.messages:
            if isinstance(message, AssistantMessage):
                for part in message.content:
                    if isinstance(part, ToolCall):
                        pending[part.id] = part.name
            elif isinstance(message, ToolResultMessage):
                pending.pop(message.tool_call_id, None)
        return {**pending, **self._unconfirmed_tools}

    def dump(self, *, recovery: bool = False) -> dict[str, Any]:
        """Export a versioned JSON-only session, without runtime dependencies.

        Ordinary export requires an idle, completely settled history. Explicit
        recovery export also permits an interrupted/busy state, but is marked
        non-resumable: it never authorizes replay of uncertain side effects.
        The host saves the returned data; there is no persistence event or ACK.
        Dump remains available after aclose(), which is not serialized.
        """
        busy = not self._idle_event.is_set()
        pending = self._unfinished_tools()
        if not recovery and (busy or pending):
            raise RuntimeError("Session is not at a safe snapshot boundary; use dump(recovery=True) for diagnosis")
        return self._snapshot_data(recovery=recovery, run_active=busy or self._restored_run_active)

    def _snapshot_data(self, *, recovery: bool = False, run_active: bool = False) -> dict[str, Any]:
        pending = self._unfinished_tools()
        active = self._active_turn_slugs or tuple(skill.slug for skill in self._resume_turn_skills)
        turn_skills = self._turn_skills if run_active or not active else ()
        skills = capture_skill_snapshots(self._active_skill_slugs, turn_skills, self._resolved_skills,
                                         active_turn_slugs=active if run_active else ())
        state = self._state if recovery else replace(self._state, is_streaming=False, stream_message=None,
                                                     pending_tool_calls=frozenset())
        return encode_snapshot(
            state, base_system_prompt=self._base_system_prompt,
            options=self._options, skills=skills, compaction=self._compaction_state,
            settings={"tool_execution": self._tool_execution, "steering_mode": self._steering_mode.value,
                      "follow_up_mode": self._follow_up_mode.value},
            session_id=self._session_id, run_id=self._run_id or None, run_active=run_active,
            interrupted=recovery or bool(pending),
            interrupted_tools=tuple(InterruptedTool(call_id, name) for call_id, name in pending.items()),
        )

    def restore(self, session: Mapping[str, Any], *, allowed_models: Sequence[str] | None = None) -> None:
        """Validate then atomically adopt data; do not start a model or execute tools.

        Versionless old snapshots retain partial-patch semantics. New snapshots
        bind skills through this Agent's trusted manager and keep its tools,
        permissions, provider and runtime resources. An optional host model
        allowlist restricts the saved model; metadata-capable adapters must also
        recognize it. Neither snapshots nor model IDs confer provider authority.
        """
        self._ensure_open()
        if not self._idle_event.is_set():
            raise RuntimeError("Cannot restore during an active run")
        raw = dict(session)
        versioned = "format" in raw or "version" in raw
        if not versioned:
            values = decode_legacy_snapshot(raw).values
            model = values.get("model") or self._state.model
            self._validate_restored_model(model, allowed_models)
            # All data is validated before changing the Agent. Legacy composed
            # skill prefixes cannot be separated reliably; no fidelity invented.
            messages = (tuple(dicts_to_messages(values["messages"] or []))
                        if "messages" in values else self._state.messages)
            prompt = values.get("system_prompt", self._base_system_prompt)
            if isinstance(prompt, list):
                prompt = tuple(SystemPromptBlock(text=b["text"], cache=b.get("cache", False)) for b in prompt)
            options = replace(self._options, **{key: values[key] for key in
                              ("thinking", "temperature", "max_tokens") if key in values})
            self._base_system_prompt = prompt
            self._options = options
            self._update_state(messages=messages, model=model)
            self._recompose_system_prompt()
            if "messages" in values:
                self._unconfirmed_tools.clear()
                self._compaction_state = CompactionSnapshot()
            return

        candidate = decode_snapshot(raw)
        candidate.require_resumable()
        if candidate.compaction.generation:
            expected = SUMMARY_TEMPLATE.format(summary=candidate.compaction.summary)
            if (not candidate.messages or not isinstance(candidate.messages[0], UserMessage)
                    or candidate.messages[0].content != expected):
                raise ValueError("Compaction summary does not match the saved working history")
        self._validate_restored_model(candidate.model, allowed_models)
        bindings = {}
        for record in candidate.skills:
            if record.lifecycle != "consumed":
                bindings[record.slug] = self._skills.load_skill(record.slug) if self._skills is not None else None
        skills = validate_skill_bindings(candidate, bindings)
        fixed = _skill_preamble([skill for skill in skills if not skill.expires_after_turn])
        # Keep runtime-only options (including host credentials/headers/cancel)
        # from the current host; restore only the decoder's explicit whitelist.
        option_fields = raw["options"]
        options = replace(self._options, **{key: getattr(candidate.options, key) for key in option_fields})
        settings = candidate.settings
        self._base_system_prompt = candidate.base_system_prompt
        self._fixed_preamble = fixed
        self._resolved_skills = {skill.slug: skill for skill in skills}
        self._active_skill_slugs = tuple(skill.slug for skill in skills)
        pending_slugs = {record.slug for record in candidate.skills if record.lifecycle == "pending"}
        active_slugs = {record.slug for record in candidate.skills
                        if record.scope == "turn" and record.lifecycle == "active"}
        self._turn_skills = tuple(skill for skill in skills if skill.slug in pending_slugs)
        self._resume_turn_skills = tuple(skill for skill in skills if skill.slug in active_slugs)
        self._restored_run_active = candidate.run_active
        self._compaction_state = candidate.compaction
        self._session_id = candidate.session_id or self._session_id
        self._run_id = candidate.run_id or ""
        self._event_sequence = 0
        self._options = options
        self._tool_execution = settings.get("tool_execution", self._tool_execution)  # type: ignore[assignment]
        self._steering_mode = QueueMode(settings.get("steering_mode", self._steering_mode.value))
        self._follow_up_mode = QueueMode(settings.get("follow_up_mode", self._follow_up_mode.value))
        self._unconfirmed_tools.clear()
        self.clear_all_queues()
        self._update_state(messages=candidate.messages, model=candidate.model, error=candidate.error,
                           stream_message=None, pending_tool_calls=frozenset())
        self._recompose_system_prompt()

    def _validate_restored_model(self, model: str, allowed_models: Sequence[str] | None) -> None:
        if allowed_models is not None and model not in allowed_models:
            raise ValueError("Saved model is not allowed by the current host")
        if model and isinstance(self._stream_fn, ModelStreamFn) and self._stream_fn.resolve_model(model) is None:
            raise ValueError("Saved model is unavailable on the current host adapter")

    def reset(self) -> None:
        """Reset the agent to a clean state (messages, error, streaming)."""
        self._update_state(
            messages=(),
            error=None,
            is_streaming=False,
            stream_message=None,
            pending_tool_calls=frozenset(),
        )
        self.clear_all_queues()
        self._unconfirmed_tools.clear()
        self._compaction_state = CompactionSnapshot()

    # -- Queue management ----------------------------------------------------

    def clear_steering_queue(self) -> None:
        """Discard all pending steering messages."""
        self._steering_queue.clear()
        self._forget_inputs("steer")

    def clear_follow_up_queue(self) -> None:
        """Discard all pending follow-up messages."""
        self._follow_up_queue.clear()
        self._forget_inputs("follow_up")

    def clear_all_queues(self) -> None:
        """Discard all pending steering and follow-up messages."""
        self._steering_queue.clear()
        self._follow_up_queue.clear()
        self._identified_inputs.clear()
        self._input_message_ids.clear()

    def has_queued_messages(self) -> bool:
        """Return ``True`` if any steering or follow-up messages are queued."""
        return bool(self._steering_queue or self._follow_up_queue)

    # -- Event system --------------------------------------------------------

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        """Register an event listener.  Returns an unsubscribe function."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _emit(self, event: _EventT) -> _EventT:
        """Correlate and dispatch an observation; callbacks are not storage ACKs."""
        self._event_sequence += 1
        event = replace(event, session_id=self._session_id, run_id=self._run_id, sequence=self._event_sequence)
        if isinstance(event, AgentEndEvent):
            self._end_emitted = True
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                logger.exception("Error in agent event listener")
        return event

    # -- Prompt / Continue ---------------------------------------------------

    async def prompt(
        self,
        message: str | AgentMessage | list[AgentMessage],
        images: list[ImageContent] | None = None,
        *, initial_prompt_context: Callable[[], Awaitable[None]] | None = None,
    ) -> AgentEventStream[AgentEvent]:
        """Start a new agent turn with a user message.

        Supports multiple overloads matching the TS Agent:
        - ``prompt("text")`` — creates a UserMessage with text content.
        - ``prompt("text", images=[...])`` — creates a UserMessage with text + images.
        - ``prompt(agent_message)`` — uses the message directly.
        - ``prompt([msg1, msg2, ...])`` — multiple messages at once.

        Returns
        -------
        AgentEventStream[AgentEvent]
            Async iterable of agent lifecycle events.
        """
        self._ensure_open()
        if self._state.is_streaming or not self._idle_event.is_set():
            raise RuntimeError(
                "Agent is already processing a prompt. "
                "Use steer() or follow_up() to queue messages, or wait for completion."
            )

        if not self._state.model:
            raise RuntimeError("No model configured")

        msgs: list[AgentMessage]

        if isinstance(message, list):
            msgs = message
        elif isinstance(message, str):
            content_parts: list[TextContent | ImageContent] = [TextContent(text=message)]
            if images:
                content_parts.extend(images)
            msgs = [UserMessage(content=tuple(content_parts))]
        else:
            msgs = [message]

        if self._resume_turn_skills:
            retired = {skill.slug for skill in self._resume_turn_skills}
            self._active_skill_slugs = tuple(slug for slug in self._active_skill_slugs if slug not in retired)
            self._resume_turn_skills = ()
        self._restored_run_active = False
        return await self._run_loop(msgs, initial_prompt_context=initial_prompt_context)

    async def continue_(self) -> AgentEventStream[AgentEvent]:
        """Continue from current context (used for retries and resuming queued messages).

        Matches the TS ``continue()`` method:
        - If last message is assistant, drains steering queue first, then follow-up.
        - Otherwise, continues from the existing context.
        """
        self._ensure_open()
        if self._state.is_streaming or not self._idle_event.is_set():
            raise RuntimeError(
                "Agent is already processing. Wait for completion before continuing."
            )

        current_messages = list(self._state.messages)
        if not current_messages:
            raise RuntimeError("No messages to continue from")

        last = current_messages[-1]
        if isinstance(last, AssistantMessage):
            # Drain steering queue first
            steering = self._dequeue_steering()
            if steering:
                return await self._run_loop(steering, skip_initial_steering_poll=True, input_source="steer")

            # Then try follow-up queue
            follow_ups = self._dequeue_follow_ups()
            if follow_ups:
                return await self._run_loop(follow_ups, input_source="follow_up")

            raise RuntimeError("Cannot continue from message role: assistant")

        return await self._run_loop(None, input_source="resume" if self._restored_run_active else "continue")

    # -- Steering & follow-up ------------------------------------------------

    def steer(self, message: str | AgentMessage) -> None:
        """Queue a steering interrupt.

        The next tool-execution checkpoint will pick up this message and
        skip remaining tool calls, letting the model respond to the
        steering input instead.
        """
        self._ensure_open()
        if isinstance(message, str):
            msg: AgentMessage = UserMessage(content=message)
        else:
            msg = message
        self._steering_queue.append(msg)

    def follow_up(self, message: str | AgentMessage) -> None:
        """Queue a follow-up message.

        Follow-ups are appended after all tool calls in a turn complete,
        causing the model to continue with the additional context.
        """
        self._ensure_open()
        if isinstance(message, str):
            msg: AgentMessage = UserMessage(content=message)
        else:
            msg = message
        self._follow_up_queue.append(msg)

    def queue_custom_message(self, message: CustomAgentMessage, delivery: str, *,
                             current: Callable[[], bool], prepare: Callable[[], Awaitable[None]],
                             persist: Callable[[], Awaitable[None]], rejected: Callable[[], None]) -> None:
        """Queue canonical custom data at existing tool boundaries, never user input."""
        self._ensure_open()
        if (not self.is_streaming or delivery not in {'steer', 'follow_up'} or not current()
                or len(self._custom_queue) >= 32):
            raise ValueError('Custom delivery requires its original active agent and queue capacity')
        self._custom_queue[id(message)] = current, prepare, persist, rejected
        (self._steering_queue if delivery == 'steer' else self._follow_up_queue).append(message)

    def queue_input(self, input_id: str, content: str, delivery: Delivery,
                    images: tuple[ImageContent, ...] = ()) -> QueuedInput:
        """Identify an input on the owning loop; acceptance is not consumption."""
        self._ensure_open()
        if not input_id or len(input_id) > 128 or input_id in self._identified_inputs:
            raise ValueError("Input ID is missing, too long, or already queued")
        if delivery not in {"steer", "follow_up"} or not (content.strip() or images):
            raise ValueError("Invalid input delivery or empty content")
        item = QueuedInput(input_id, content, delivery, images=images)
        message = UserMessage(content=(TextContent(text=content), *images) if images else content)
        self._identified_inputs[input_id] = (item, message)
        self._input_message_ids[id(message)] = input_id
        (self._steering_queue if delivery == "steer" else self._follow_up_queue).append(message)
        return item

    def pending_inputs(self) -> tuple[QueuedInput, ...]:
        """Snapshot queued/selected identified inputs; call on the owning loop."""
        ordered: list[QueuedInput] = []
        for message in (*self._steering_queue, *self._follow_up_queue):
            input_id = self._input_message_ids.get(id(message))
            if input_id is not None:
                ordered.append(self._identified_inputs[input_id][0])
        ordered.extend(item for item, _message in self._identified_inputs.values() if item.selected)
        return tuple(ordered)

    def change_input(self, input_id: str, action: str, content: str = "", direction: int = 0) -> bool:
        """Edit/remove/reorder only while queued; checkpoint-selected inputs refuse edits."""
        record = self._identified_inputs.get(input_id)
        if record is None or record[0].selected:
            return False
        item, message = record
        queue = self._steering_queue if item.delivery == "steer" else self._follow_up_queue
        position = next((i for i, value in enumerate(queue) if value is message), None)
        if position is None:
            return False
        if action == "remove":
            queue.pop(position)
            self._identified_inputs.pop(input_id)
            self._input_message_ids.pop(id(message), None)
        elif action == "edit" and content.strip():
            replacement = UserMessage(content=(TextContent(text=content), *item.images) if item.images else content)
            queue[position] = replacement
            self._input_message_ids.pop(id(message), None)
            self._input_message_ids[id(replacement)] = input_id
            self._identified_inputs[input_id] = (replace(item, content=content), replacement)
        elif action == "move" and direction in {-1, 1}:
            target = position + direction
            if not 0 <= target < len(queue):
                return False
            queue[position], queue[target] = queue[target], queue[position]
        else:
            return False
        return True

    def drain_inputs(self, prefix: str = "") -> tuple[QueuedInput, ...]:
        """After a run settles, remove/return inputs not admitted to history."""
        items = tuple(item for item in self.pending_inputs() if item.id.startswith(prefix))
        keys = {item.id for item in items}
        identities = {identity for identity, key in self._input_message_ids.items() if key in keys}
        self._steering_queue[:] = [message for message in self._steering_queue if id(message) not in identities]
        self._follow_up_queue[:] = [message for message in self._follow_up_queue if id(message) not in identities]
        for key in keys:
            _item, message = self._identified_inputs.pop(key)
            self._input_message_ids.pop(id(message), None)
        return items

    def _forget_inputs(self, delivery: Delivery) -> None:
        for input_id, (item, message) in tuple(self._identified_inputs.items()):
            if item.delivery == delivery:
                self._identified_inputs.pop(input_id)
                self._input_message_ids.pop(id(message), None)

    def _select_inputs(self, messages: list[AgentMessage]) -> list[AgentMessage]:
        for message in messages:
            input_id = self._input_message_ids.get(id(message))
            if input_id is not None:
                item, queued_message = self._identified_inputs[input_id]
                self._identified_inputs[input_id] = (replace(item, selected=True), queued_message)
        return messages

    # -- Abort ---------------------------------------------------------------

    def abort(self) -> None:
        """Request cancellation safely from any thread."""
        with self._cancel_lock:
            loop = self._cancel_loop
            token = self._cancel_token
            if loop is None:
                # The intended run has already settled (or has not started).
                # Never carry a stale UI cancellation into a successor turn.
                return

        def _cancel_current() -> None:
            with self._cancel_lock:
                if token != self._cancel_token:
                    return
                self._cancel.set()

        try:
            try:
                local = asyncio.get_running_loop() is loop
            except RuntimeError:
                local = False
            if local:
                _cancel_current()  # Budget/listener veto must stop the NEXT tool checkpoint immediately.
            else:
                loop.call_soon_threadsafe(_cancel_current)
        except RuntimeError:
            # The owning run settled and closed its loop after we released the
            # lock. Treat this abort as stale, not as cancellation of a future run.
            return

    def reset_abort(self) -> None:
        """Clear a pending pre-run abort without replacing an active event."""
        with self._cancel_lock:
            if self._cancel_loop is None:
                self._pending_abort = False

    # -- Wait for idle -------------------------------------------------------

    async def wait_for_idle(self) -> None:
        """Wait until the current agent loop finishes.

        Returns immediately if the agent is not streaming.
        """
        await self._idle_event.wait()

    async def cancel_and_wait(self) -> None:
        """Cancel and settle the owned producer, including cooperative hook cleanup.

        Unlike abort(), this forcibly cancels its asyncio task. Call on the owning
        loop; it cannot terminate blocking/native code or roll back tool effects.
        """
        task = self._producer_task
        if task is None or task.done():
            return
        if task.get_loop() is not asyncio.get_running_loop():
            raise RuntimeError("Producer settlement must run on the owning loop")
        # A task canceled before its first step never executes its finally block.
        # Admit the producer first so idle/stream ownership always settles.
        await self._producer_started.wait()
        if self._producer_task is not task or task.done():
            return  # The captured run settled; never abort a successor.
        self.abort()
        if not task.cancelling():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    # -- Internal: queue draining --------------------------------------------

    def _dequeue_steering(self) -> list[AgentMessage]:
        """Drain the steering queue based on the configured mode."""
        if not self._steering_queue:
            return []

        if self._steering_mode == QueueMode.ALL:
            msgs = list(self._steering_queue)
            self._steering_queue.clear()
            return self._select_inputs(msgs)

        # ONE_AT_A_TIME
        return self._select_inputs([self._steering_queue.pop(0)])

    def _dequeue_follow_ups(self) -> list[AgentMessage]:
        """Drain the follow-up queue based on the configured mode."""
        if not self._follow_up_queue:
            return []

        if self._follow_up_mode == QueueMode.ALL:
            msgs = list(self._follow_up_queue)
            self._follow_up_queue.clear()
            return self._select_inputs(msgs)

        return self._select_inputs([self._follow_up_queue.pop(0)])

    # -- Internal: queue callbacks for agent_loop ----------------------------

    async def _get_steering_messages(self) -> list[AgentMessage]:
        """Drain the steering queue (async version for agent_loop config)."""
        return await self._prepare_inputs(self._dequeue_steering(), "steer")

    async def _get_follow_up_messages(self) -> list[AgentMessage]:
        """Drain the follow-up queue (async version for agent_loop config)."""
        return await self._prepare_inputs(self._dequeue_follow_ups(), "follow_up")

    async def _prepare_inputs(self, messages: list[AgentMessage], source: str) -> list[AgentMessage]:
        if self._input_transform is None and not self._custom_queue:
            return messages
        prepared: list[AgentMessage] = []
        receipts: list[tuple[UserMessage, UserMessage, str, str]] = []
        handled: set[int] = set()
        try:
            for message in messages:
                if not isinstance(message, UserMessage):
                    custom = self._custom_queue.get(id(message))
                    if custom is not None and (self._cancel.is_set() or not custom[0]()):
                        self._custom_queue.pop(id(message), None)
                        custom[3]()
                        continue
                    prepared.append(message)
                    continue
                if self._input_transform is None:
                    prepared.append(message)
                    continue
                key = self._input_message_ids.get(id(message), "")
                delivery = self._identified_inputs[key][0].delivery if key else source
                if self._cancel.is_set():
                    raise asyncio.CancelledError
                task = asyncio.ensure_future(self._input_transform(message, delivery, self._cancel))
                watcher = asyncio.create_task(self._cancel.wait())
                try:
                    done, _pending = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
                    if watcher in done:
                        raise asyncio.CancelledError
                    transformed = task.result()
                    if isinstance(transformed, HandledInput):
                        if (not isinstance(transformed.feedback, str) or not transformed.feedback.strip()
                                or len(transformed.feedback.encode()) > 65536):
                            raise ValueError("Handled input requires bounded explicit feedback")
                        completed = InputHandledEvent(message, delivery, transformed.feedback, key)
                        if self._input_checkpoint is not None:
                            await self._input_checkpoint(completed)
                        handled.add(id(message))
                        if key:
                            self._input_message_ids.pop(id(message), None)
                            self._identified_inputs.pop(key, None)
                        completed = self._emit(completed)
                        self._handled_inputs.append(completed)
                        if self._current_stream is not None:
                            await self._current_stream.send(completed)
                        if key and self._input_settled is not None:
                            await self._input_settled(self.pending_inputs())
                        continue  # Logical handler consumption, never canonical/model input admission.
                    if not isinstance(transformed, UserMessage):
                        raise ValueError("Input transform must return a UserMessage")
                    prepared.append(transformed)
                    receipts.append((message, transformed, key, delivery))
                finally:
                    if not task.done() and not (isinstance(task, asyncio.Task) and task.cancelling()):
                        task.cancel()
                    watcher.cancel()
                    await asyncio.gather(task, watcher, return_exceptions=True)
        except BaseException:
            self._input_rejected = True
            plain = [message for message in messages
                     if id(message) not in self._input_message_ids and id(message) not in handled]
            if source in {"steer", "follow_up"}:
                self._rejected_messages.extend(plain)  # Quarantine, not next-run automatic re-admission.
            for message in messages:
                if isinstance(message, UserMessage) and id(message) not in handled:
                    key = self._input_message_ids.get(id(message), "")
                    delivery = self._identified_inputs[key][0].delivery if key else source
                    event = InputRejectedEvent(message, delivery, key)
                    event = self._emit(event)
                    if self._current_stream is not None:
                        await self._current_stream.send(event)
            raise
        for original, transformed, key, delivery in receipts:
            if key and original is not transformed:
                self._input_message_ids.pop(id(original), None)
                self._input_message_ids[id(transformed)] = key
                item, _message = self._identified_inputs[key]
                self._identified_inputs[key] = item, transformed
            if original != transformed:
                changed = InputTransformedEvent(original, transformed, delivery, key)
                changed = self._emit(changed)
                if self._current_stream is not None:
                    await self._current_stream.send(changed)
        return prepared

    # -- Internal run --------------------------------------------------------

    async def _run_loop(
        self,
        messages: list[AgentMessage] | None,
        *,
        skip_initial_steering_poll: bool = False,
        input_source: str = "prompt",
        initial_prompt_context: Callable[[], Awaitable[None]] | None = None,
    ) -> AgentEventStream[AgentEvent]:
        """Build the loop config and start the agent loop."""
        self._ensure_open()
        if self._unfinished_tools():
            raise RuntimeError("Unconfirmed tool history requires host reconciliation before another run")
        loop = asyncio.get_running_loop()
        with self._cancel_lock:
            self._cancel_token += 1
            cancel_token = self._cancel_token
            self._cancel = asyncio.Event()
            self._cancel_loop = loop
            if self._pending_abort:
                self._pending_abort = False
                self._cancel.set()
            cancel_event = self._cancel
        if not (input_source == "resume" and self._restored_run_active):
            self._run_id = uuid.uuid4().hex
        self._event_sequence = 0
        self._end_emitted = False
        self._idle_event.clear()
        self._input_rejected = False

        self._update_state(
            is_streaming=True,
            stream_message=None,
            pending_tool_calls=frozenset(),
            error=None,
        )

        # A model that cannot take tools through the API must not be sent any:
        # `body["tools"]` is written whenever the context carries them, with no
        # check of its own, so the request would carry definitions the model
        # cannot use and may reject. Its tools reach it as prompt text instead —
        # which is why `supports_tool_calls=False` removes them from here and
        # not from the description.
        active_tools = self.active_tools
        _send_tools = active_tools and self._supports_tool_calls is not False
        context = AgentContext(
            system_prompt=self._state.system_prompt,
            messages=list(self._state.messages),
            tools=list(active_tools) if _send_tools else None,
        )

        # Inject cancel event into StreamOptions for this run
        from dataclasses import replace as _replace
        options = _replace(self._options, cancel=cancel_event)

        # Wrap steering callback to support skipInitialSteeringPoll
        skip_steering = skip_initial_steering_poll

        async def _steering_cb() -> list[AgentMessage]:
            nonlocal skip_steering
            if skip_steering:
                skip_steering = False
                return []
            return await self._prepare_inputs(self._dequeue_steering(), "steer")

        # Capture instruction data once. Projection is request-local and never
        # appended to canonical history, checkpoints or dumps.
        turn_snapshot = (self._turn_skills if messages is not None and input_source == "prompt" else
                         self._resume_turn_skills if input_source == "resume" else ())
        turn_messages: tuple[UserMessage, ...] = _transient_messages(turn_snapshot) if input_source == "resume" else ()
        skills_used = False

        async def skill_context(items: list[AgentMessage]) -> list[AgentMessage]:
            projected: list[AgentMessage] = [*turn_messages, *items]
            if self._transform_context is not None:
                return await self._transform_context(projected)
            return projected

        def model_requested() -> None:
            nonlocal skills_used
            if turn_messages:
                skills_used = True
                self._active_turn_slugs = tuple(skill.slug for skill in turn_snapshot)

        async def prepare_request(current: AgentContext, request: Context) -> Context:
            if not self._compaction_config.auto:
                return request
            self._compacting = True
            try:
                result = await self._compact_context(request, automatic=True, loop_context=current,
                                                     prefix=turn_messages)
                return result.context if result is not None else request
            finally:
                self._compacting = False

        config = AgentLoopConfig(
            tool_execution=self._tool_execution,
            before_tool_call=self._before_tool_call,
            after_tool_call=self._after_tool_call,
            convert_to_llm=self._convert_to_llm or _default_convert_to_llm,
            transform_context=skill_context,
            on_model_request=model_requested,
            prepare_request=prepare_request if self._compaction_config.auto else None,
            on_stream_close_error=self._stream_cleanup_errors.append,
            get_tools=lambda: self.active_tools if self._supports_tool_calls is not False else (),
            get_steering_messages=_steering_cb,
            get_follow_up_messages=self._get_follow_up_messages,
        )

        stream: AgentEventStream[AgentEvent] = AgentEventStream(self._event_buffer_size)
        self._current_stream = stream

        self._producer_started = asyncio.Event()

        async def settled_event(event: AgentEvent) -> None:
            custom = (self._custom_queue.pop(id(event.message), None)
                      if isinstance(event, MessageEndEvent) and event.message is not None else None)
            if custom is not None:
                if cancel_event.is_set() or not custom[0]():
                    custom[3]()
                    raise ValueError('Custom queue owner revoked before canonical admission')
                await custom[1]()
                if cancel_event.is_set() or not custom[0]():
                    custom[3]()
                    raise ValueError('Custom queue owner revoked during validation')
                await self._process_loop_event(event, stream)
                await custom[2]()  # Canonical durability before the next model call.
                return
            identified = (isinstance(event, MessageEndEvent) and event.message is not None
                          and id(event.message) in self._input_message_ids)
            if (isinstance(event, MessageEndEvent) and isinstance(event.message, UserMessage)
                    and self._input_checkpoint is not None):
                await self._input_checkpoint(event)
            await self._process_loop_event(event, stream)
            if identified and self._input_settled is not None:
                await self._input_settled(self.pending_inputs())

        async def _run_and_wrap() -> None:
            nonlocal skip_steering, turn_messages
            self._producer_started.set()
            owner_token = _running_agent.set(self)
            try:
                if messages is not None:
                    messages_prepared = await self._prepare_inputs(messages, input_source)
                    if turn_snapshot and any(isinstance(item, UserMessage) for item in messages_prepared):
                        turn_messages = _transient_messages(turn_snapshot)
                    # Only this originally submitted user prompt may admit a
                    # captured context watermark. Handled/rejected input, queued
                    # replacement input, continue and tool iterations never do.
                    if (initial_prompt_context is not None and input_source == 'prompt'
                            and any(isinstance(item, UserMessage) for item in messages_prepared)):
                        if cancel_event.is_set():
                            raise asyncio.CancelledError
                        context_task = asyncio.ensure_future(initial_prompt_context())
                        context_cancel = asyncio.create_task(cancel_event.wait())
                        try:
                            done, _ = await asyncio.wait({context_task, context_cancel},
                                                         return_when=asyncio.FIRST_COMPLETED)
                            if context_cancel in done:
                                raise asyncio.CancelledError
                            context_task.result()
                        finally:
                            if not context_task.done():
                                context_task.cancel()
                            context_cancel.cancel()
                            await asyncio.gather(context_task, context_cancel, return_exceptions=True)
                        if cancel_event.is_set():
                            raise asyncio.CancelledError
                        context.messages = list(self._state.messages)
                    if messages and not messages_prepared:
                        while not messages_prepared and (self._steering_queue or self._follow_up_queue):
                            if self._steering_queue:
                                messages_prepared = await self._prepare_inputs(self._dequeue_steering(), "steer")
                            else:
                                messages_prepared = await self._prepare_inputs(self._dequeue_follow_ups(), "follow_up")
                        if not messages_prepared:
                            await self._process_loop_event(AgentEndEvent(messages=()), stream)
                            return  # All newly supplied input handled; no old-history model fallback.
                        skip_steering = True  # Already selected this boundary; preserve ONE_AT_A_TIME semantics.
                    await run_agent_loop(
                        messages_prepared,
                        context,
                        config,
                        settled_event,
                        self._stream_fn,
                        self._state.model,
                        options,
                        cancel_event,
                        self._tool_ctx,
                    )
                else:
                    await run_agent_loop_continue(
                        context,
                        config,
                        settled_event,
                        self._stream_fn,
                        self._state.model,
                        options,
                        cancel_event,
                        self._tool_ctx,
                    )
            except Exception as exc:
                logger.exception("Error in agent loop")
                self._update_state(
                    is_streaming=False,
                    error=str(exc),
                )
                error_msg = AssistantMessage(
                    content=(TextContent(text=f"Agent error: {exc}"),),
                    stop_reason="error",
                    error_message=str(exc),
                )
                if not self._input_rejected:
                    self.append_message(error_msg)  # Input refusals are not fabricated assistant/provider messages.
                error_end = AgentEndEvent(
                    messages=(*self._state.messages,),
                )
                error_end = self._emit(error_end)
                stream.send_final(error_end)
            finally:
                with self._cancel_lock:
                    if cancel_token == self._cancel_token:
                        self._cancel_loop = None
                self._update_state(
                    is_streaming=False,
                    stream_message=None,
                    pending_tool_calls=frozenset(),
                )
                for identity, pending_custom in tuple(self._custom_queue.items()):
                    self._steering_queue[:] = [item for item in self._steering_queue if id(item) != identity]
                    self._follow_up_queue[:] = [item for item in self._follow_up_queue if id(item) != identity]
                    self._custom_queue.pop(identity, None)
                    pending_custom[3]()
                if skills_used:
                    retired = {skill.slug for skill in turn_snapshot}
                    self._turn_skills = tuple(skill for skill in self._turn_skills if skill.slug not in retired)
                    self._active_skill_slugs = tuple(slug for slug in self._active_skill_slugs if slug not in retired)
                self._active_turn_slugs = ()
                if skills_used:
                    self._resume_turn_skills = ()
                self._restored_run_active = False
                if not self._end_emitted:
                    stream.send_final(self._emit(AgentEndEvent(messages=self._state.messages)))
                _running_agent.reset(owner_token)
                self._idle_event.set()
                await stream.close()

        self._producer_task = asyncio.create_task(_run_and_wrap())
        return stream

    async def _process_loop_event(
        self,
        event: AgentEvent,
        stream: AgentEventStream[AgentEvent],
    ) -> None:
        """Update internal state in response to lifecycle events."""
        if isinstance(event, MessageStartEvent):
            self._update_state(stream_message=event.message)

        elif isinstance(event, MessageUpdateEvent):
            self._update_state(stream_message=event.message)

        elif isinstance(event, MessageEndEvent):
            self._update_state(stream_message=None)
            if event.message is not None:
                self.append_message(event.message)
                input_id = self._input_message_ids.pop(id(event.message), None)
                if input_id is not None:
                    item, _message = self._identified_inputs.pop(input_id)
                    consumed = InputConsumedEvent(replace(item, selected=False))
                    consumed = self._emit(consumed)
                    await stream.send(consumed)

        elif isinstance(event, ToolExecutionStartEvent):
            self._unconfirmed_tools[event.tool_call_id] = event.tool_name
            pending = self._state.pending_tool_calls | frozenset({event.tool_call_id})
            self._update_state(pending_tool_calls=pending)

        elif isinstance(event, ToolExecutionEndEvent):
            if not event.interrupted:
                self._unconfirmed_tools.pop(event.tool_call_id, None)
            pending = self._state.pending_tool_calls - frozenset({event.tool_call_id})
            self._update_state(pending_tool_calls=pending)

        elif isinstance(event, TurnEndEvent):
            if (event.message is not None
                    and isinstance(event.message, AssistantMessage)
                    and event.message.error_message):
                self._update_state(error=event.message.error_message)

        elif isinstance(event, AgentEndEvent):
            self._update_state(
                is_streaming=False,
                stream_message=None,
            )

        event = self._emit(event)
        if isinstance(event, AgentEndEvent):
            stream.send_final(event)
        else:
            await stream.send(event)
        if isinstance(event, TurnEndEvent) and isinstance(event.message, AssistantMessage):
            usage = self._emit(UsageEvent(usage=event.message.usage, model=self._state.model))
            await stream.send(usage)
            if self._emit_snapshots and not self._unfinished_tools():
                active = bool(event.tool_results) or self.has_queued_messages()
                data = self._snapshot_data(run_active=active)
                snapshot = self._emit(SnapshotEvent(snapshot_json=json.dumps(data, ensure_ascii=False)))
                await stream.send(snapshot)


# ---------------------------------------------------------------------------
# Default converter
# ---------------------------------------------------------------------------

def _default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Project named canonical context content; keep unknown custom metadata out of the LLM."""
    from xdog.agent.extensions.context_message import llm_message
    from xdog.agent.external_event import llm_message as external_message
    from xdog.ai.types import AssistantMessage, ToolResultMessage, UserMessage

    result: list[Message] = []
    for message in messages:
        if isinstance(message, (UserMessage, AssistantMessage, ToolResultMessage)):
            result.append(message)
        elif (projected := llm_message(message)) is not None:
            result.append(projected)
        elif (external := external_message(message)) is not None:
            result.append(external)
    return result
