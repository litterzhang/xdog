"""Claw-owned child execution, persistence and cancellation on the existing host loop.

Agent supplies SDK types and tools only; this module owns every child lifecycle.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import inspect
import json
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar

from xdog.agent.agent import Agent
from xdog.agent.child_compaction import ArchivedSummaryStream, preserve_model_metadata
from xdog.agent.child_config import ChildConfig, ChildPresets
from xdog.agent.child_images import (
    check_collection,
    check_retained,
    image_parts,
    images_from_dicts,
    images_to_dicts,
    input_receipt,
    text_size,
    validate_images,
)
from xdog.agent.children import (
    MAX_TRANSCRIPT_BYTES,
    RESERVED_TOOLS,
    ChildCapture,
    ChildClient,
    ChildSpec,
    ChildToolProgress,
    child_message_status,
    child_prompt,
    child_task,
)
from xdog.agent.core import AgentMessage, AgentTool, ModelStreamFn
from xdog.agent.cross_loop import settle_on, submit_remote
from xdog.agent.events import (
    AgentEvent,
    CompactionEndEvent,
    CompactionFailedEvent,
    CompactionStartEvent,
    InputConsumedEvent,
    InputHandledEvent,
    InputRejectedEvent,
    MessageEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionUpdateEvent,
    UsageEvent,
)
from xdog.agent.history import history_points, validate_checkpoint
from xdog.agent.input_queue import Delivery, QueuedInput
from xdog.agent.messages import dicts_to_messages, messages_to_dicts
from xdog.agent.snapshots import decode_snapshot_messages, decode_snapshot_parts, encode_snapshot_messages
from xdog.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    StreamOptions,
    SystemPrompt,
    TextContent,
    ToolCall,
    UserMessage,
)
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.runtime.child_returns import LiveImageReturns

T = TypeVar("T")


def _serialized_size(messages: tuple[AgentMessage, ...]) -> int:
    try:
        return text_size(messages)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Child transcript is not JSON serializable") from exc


@dataclass
class _Child:
    spec: ChildSpec
    agent: Agent | None = None
    task: asyncio.Task[None] | None = None
    status: str = "queued"
    result: str = ""
    input_feedback: tuple[str, ...] = ()
    error: str = ""
    calls: int = 0
    tokens: int = 0
    updated: float = field(default_factory=time.time)
    transcript: tuple[AgentMessage, ...] = ()
    settling: bool = True
    revision: int = 0
    partial: AgentMessage | None = None
    progress: dict[str, ChildToolProgress] = field(default_factory=dict)
    progress_sources_limited: bool = False
    effective: tuple[tuple[str, Any], ...] = ()
    undelivered: tuple[QueuedInput, ...] = ()
    queue_work: bool = False
    queue_uncertain: bool = False
    returned_backups: tuple[QueuedInput, ...] = ()
    initial_receipt: dict[str, Any] = field(default_factory=dict)
    initial_state: str = 'pending'
    live_returns: LiveImageReturns = field(default_factory=LiveImageReturns)
    prompt_changed: bool = False
    message_ids: set[str] = field(default_factory=set)
    initial_message_observed: bool = False
    session_state: dict[str, Any] | None = None
    tool_schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    continuation_note: str = ""


class ClawChildRuntime:
    """Host-owned children, with factory-opted-in native Agent compaction.

    Auto-compacting factories must provide archive_compaction: a synchronous
    durable export of (spec, generation, complete original paired history).
    It runs as owned thread work before the native summary request may start.
    A missing/failing archive never permits an unarchived compaction commit.
    """

    def __init__(
        self,
        factory: Callable[[ChildSpec, Callable[[dict[str, Any]], None]], Agent | Awaitable[Agent]],
        persist: Callable[[dict[str, Any]], None] | None = None,
        *, capture: Callable[[ChildSpec], ChildSpec | None | Awaitable[ChildSpec | None]] | None = None,
        begin: Callable[[ChildSpec], None] | None = None,
        persist_inputs: Callable[[str, tuple[QueuedInput, ...]], None] | None = None,
        persist_return: Callable[[str, QueuedInput], None] | None = None,
        persist_admission: Callable[[dict[str, Any]], None] | None = None,
        archive_compaction: Callable[[ChildSpec, int, tuple[AgentMessage, ...]], None] | None = None,
        forget: Callable[[str], None] | None = None,
        finalize: Callable[[ChildSpec], Awaitable[None]] | None = None,
        on_event: Callable[[ChildSpec, AgentEvent], Awaitable[None]] | None = None,
        records: list[dict[str, Any]] | None = None,
        presets: ChildPresets | None = None,
        owner_loop: Callable[[], asyncio.AbstractEventLoop | None],
    ) -> None:
        self.factory = factory
        self.presets = presets or ChildPresets()
        self.capture = capture or (lambda spec: None)
        self.begin = begin or (lambda spec: None)
        self.persist_inputs = persist_inputs
        self.persist_return = persist_return
        self.persist_admission = persist_admission
        self.archive_compaction = archive_compaction
        self.forget = forget or (lambda identifier: None)
        self.on_event = on_event
        self.finalize = finalize
        self.persist = persist or (lambda record: None)
        self.diagnostic = ""
        self.publish: Callable[[dict[str, Any]], None] = lambda event: None
        self._listeners: dict[object, Callable[[dict[str, Any]], None]] = {}
        self._children: dict[str, _Child] = {}
        self._admissions: set[asyncio.Task[Any]] = set()
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._owner_loop = owner_loop
        self._closed = False
        self._close_future: concurrent.futures.Future[None] | asyncio.Task[None] | None = None
        # Process-local main-Agent ownership, never an argument, result or durable record.
        # tools() is refreshed by hosts; the nonce must survive those refreshes.
        self._tool_return_owner = uuid.uuid4().hex
        self._admission_pauses = 0
        from xdog.agent.child_queue import recovery_from_dicts
        for record in records or ():
            config = ChildConfig.from_record(record.get("config", {}))
            spec = ChildSpec(id=record["id"], task=record["task"], access=record["access"],
                             inherit_context=config.inherit_context, label=config.label,
                             instructions=config.instructions, tools=config.tools, model=config.model,
                             thinking=config.thinking, preset=config.preset,
                             images=images_from_dicts(record.get('task_images', [])),
                             system_prompt=config.system_prompt, skills=config.skills,
                             tool_actions=config.tool_actions, tool_scopes=copy.deepcopy(config.tool_scopes))
            self._children[spec.id] = _Child(
                spec, status=record["status"], result=record.get("result", ""), error=record.get("error", ""),
                calls=record.get("calls", 0), tokens=record.get("tokens", 0), updated=record.get("updated", 0),
                transcript=(decode_snapshot_messages(record.get("messages", []),
                            interrupted=record["status"] != "completed") if "session_state" in record
                            else tuple(dicts_to_messages(record.get("messages", [])))), settling=False,
                effective=tuple(record.get("effective", {}).items()),
                input_feedback=tuple(record.get("input_feedback", ())),
                undelivered=recovery_from_dicts(record.get('undelivered_inputs', [])),
                queue_uncertain=record.get('queue_uncertain', False),
                returned_backups=recovery_from_dicts(record.get('returned_backups', [])),
                initial_state=record.get('initial_state', 'uncertain' if record.get('task_images')
                                        or not record.get('messages') else 'admitted'),
                session_state=copy.deepcopy(record.get("session_state")),
                tool_schemas=copy.deepcopy(record.get("tool_schemas", {})),
                continuation_note=record.get("continuation_note", ""),
            )

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._start_lock:
            if self._closed:
                raise RuntimeError("Claw child runtime closed")
            loop = self._loop or self._owner_loop()
            if loop is None or loop.is_closed() or not loop.is_running():
                raise RuntimeError("Claw child runtime needs its active host owner loop")
            self._loop = loop
            return loop

    async def _bridge(self, coroutine: Coroutine[Any, Any, T]) -> T:
        try:
            # Resolve the host port on the calling owner, not in the acquisition
            # worker (a trusted callback may use get_running_loop). No loop is
            # created here, and an already bound runtime can never be moved.
            candidate = self._owner_loop()
            with self._start_lock:
                if self._loop is not None and self._loop is not candidate:
                    raise RuntimeError("Claw child runtime cannot move to a successor host loop")
                self._loop = candidate
            loop = await asyncio.to_thread(self._ensure_loop)
        except BaseException:
            coroutine.close()
            raise
        with self._start_lock:
            if self._closed:
                coroutine.close()
                raise RuntimeError("Claw child runtime closed")
            if loop is not asyncio.get_running_loop():
                pending = submit_remote(loop, coroutine)
            else:
                pending = coroutine
        return await pending

    @contextmanager
    def idle_scope(self) -> Iterator[None]:
        """Freeze new admission throughout a host's idle-only policy/context change."""
        with self._lock:
            if self.active:
                raise RuntimeError("Child work is active; cancel/wait before changing session context/policy")
            self._admission_pauses += 1
        try:
            yield
        finally:
            with self._lock:
                self._admission_pauses -= 1

    @property
    def active(self) -> bool:
        with self._lock:
            return bool(self._admissions) or any(child.settling for child in self._children.values())

    def capture_source(self, identifier: str) -> ChildCapture:
        """Small immutable reference snapshot; serialization belongs off the UI/host loop."""
        with self._lock:
            child = self._children.get(identifier)
            if child is None:
                raise ValueError("Child unavailable in this session")
            return ChildCapture(child.spec.id, child.spec.task, child.spec.access, child.status, child.calls,
                                child.tokens, child.revision, child.settling, child.error,
                                child.transcript, child.partial, tuple(child.progress.values()),
                                child.progress_sources_limited, child.spec.config(), child.effective,
                                input_feedback=child.input_feedback)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result: dict[str, Any] = {
                "children": [self._record(child, transcript=False) for child in self._children.values()]}
            if self.diagnostic:
                result["error"] = self.diagnostic
            return result

    def _record(self, child: _Child, *, transcript: bool) -> dict[str, Any]:
        with self._lock:
            record = {"id": child.spec.id, "task": child.spec.task if transcript else child.spec.task[:400],
                      "access": child.spec.access,
                      "status": child.status, "result": child.result if transcript else child.result[:256],
                      "error": child.error,
                      "calls": child.calls, "tokens": child.tokens, "updated": child.updated,
                      "settling": child.settling, "revision": child.revision,
                      "config": child.spec.config().to_dict() if transcript else {
                          "label": child.spec.label,
                          "inherit_context": child.spec.inherit_context, "model": child.spec.model,
                          "thinking": child.spec.thinking, "preset": child.spec.preset}}
            if child.effective:
                record["effective"] = dict(child.effective)
            if child.continuation_note:
                record["continuation_note"] = child.continuation_note
            if transcript:
                record['initial_state'] = child.initial_state
                if child.session_state is not None:
                    record["session_state"] = copy.deepcopy(child.session_state)
                    record["tool_schemas"] = copy.deepcopy(child.tool_schemas)
                if child.queue_uncertain:
                    record['queue_uncertain'] = True
                retained = set(image_parts(child.transcript))
                missing = (child.spec.images if child.initial_state in {'pending', 'uncertain'} else
                           tuple(part for part in child.spec.images if part not in retained))
                if missing:
                    record['task_images'] = images_to_dicts(missing)
                if child.undelivered:
                    from xdog.agent.child_queue import recovery_to_dicts
                    record['undelivered_inputs'] = recovery_to_dicts(child.undelivered)
                if child.returned_backups:
                    from xdog.agent.child_queue import recovery_to_dicts
                    record['returned_backups'] = recovery_to_dicts(child.returned_backups)
                if child.input_feedback:
                    record["input_feedback"] = list(child.input_feedback)
                record["messages"] = (encode_snapshot_messages(child.transcript,
                    interrupted=child.status != "completed") if child.session_state is not None
                    else messages_to_dicts(list(child.transcript)))
                if child.progress_sources_limited:
                    record["live_tools_limited"] = True
                if child.progress:
                    record["live_tools"] = [row.to_dict() for row in child.progress.values()]
                if child.partial is not None:
                    record["partial"] = messages_to_dicts([child.partial])[0]
                points = history_points(child.transcript)
                record["checkpoint"] = points[-1].index if points else -1
            return record

    async def start(self, task: str, *, access: str = "read-only", inherit_context: bool = False,
                    config: dict[str, Any] | None = None,
                    images: tuple[ImageContent, ...] = ()) -> str:
        await asyncio.to_thread(validate_images, images)
        if (not isinstance(task, str) or not (task.strip() or images) or len(task.encode("utf-8")) > 16000
                or not isinstance(access, str) or access not in {"read-only", "inherit"}
                or type(inherit_context) is not bool or inherit_context):
            raise ValueError("Invalid child task/access/context")
        if config is not None:
            ChildConfig.from_dict(config)
        base: dict[str, Any] = {"inherit_context": inherit_context}
        selected = self.presets.resolve({**base, **(config or {})})
        receipt = await asyncio.to_thread(input_receipt, task, images)
        return await self._bridge(self._start(task, access, selected, images, receipt))

    async def _start(self, task: str, access: str, config: ChildConfig, images: tuple[ImageContent, ...],
                     receipt: dict[str, Any]) -> str:
        # Own only the finite admission operation. The outer request may enter
        # its own finally/aclose after cancellation and must never be joined by
        # runtime shutdown (which would create a request <-> close cycle).
        operation = asyncio.create_task(self._admit(task, access, config, images, receipt))
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            if not operation.done() and not operation.cancelling():
                operation.cancel()
            from xdog.agent.async_work import wait_owned
            try:
                await wait_owned(operation)
            except asyncio.CancelledError:
                pass
            raise

    async def _admit(self, task: str, access: str, config: ChildConfig, images: tuple[ImageContent, ...],
                     receipt: dict[str, Any]) -> str:
        operation = asyncio.current_task()
        assert operation is not None
        with self._lock:
            if self._closed:
                raise RuntimeError("Claw child runtime closed")
            if self._admission_pauses:
                raise RuntimeError("Session context/policy is changing; refresh before starting child work")
            if self.diagnostic:
                raise ValueError(self.diagnostic)
            self._admissions.add(operation)
        spec = ChildSpec(id=uuid.uuid4().hex[:12], task=task, access=access, inherit_context=False,
                        label=config.label, instructions=config.instructions, tools=config.tools,
                        model=config.model, thinking=config.thinking, preset=config.preset, images=images,
                        system_prompt=config.system_prompt, skills=config.skills,
                             tool_actions=config.tool_actions, tool_scopes=copy.deepcopy(config.tool_scopes))
        try:
            # This is the existing host admission operation, not a child registry
            # entry/model/worker. No thread lock spans awaited catalog validation.
            captured = self.capture(spec)
            if inspect.isawaitable(captured):
                captured = await captured
            if isinstance(captured, ChildSpec):
                spec = captured
            with self._lock:
                if self._closed or self._admission_pauses:
                    raise RuntimeError("Claw child admission owner closed or policy changed during preparation")
                if self.diagnostic:
                    raise ValueError(self.diagnostic)
                child = _Child(spec, initial_receipt=receipt)
                self._children[spec.id] = child
                child.task = asyncio.create_task(self._run(child))
                self._watch_task(child.task)
        except BaseException:
            if self.finalize is not None:
                from xdog.agent.async_work import wait_owned
                await wait_owned(asyncio.ensure_future(self.finalize(spec)))
            raise
        finally:
            with self._lock:
                self._admissions.discard(operation)
        self._notify(child)
        return spec.id

    def delegation_schemas(self, identifier: str) -> dict[str, dict[str, Any]]:
        """Data-only ceiling; the factory must still authorize actual current parent tools."""
        with self._lock:
            child = self._children.get(identifier)
            return copy.deepcopy(child.tool_schemas) if child is not None else {}

    def admission_receipt(self, identifier: str) -> dict[str, Any]:
        with self._lock:
            child = self._children.get(identifier)
            if child is None or not child.initial_receipt:
                raise ValueError("Child admission receipt unavailable; no reconstructed receipt")
            return {key: list(value) if isinstance(value, list) else value
                    for key, value in child.initial_receipt.items()}

    def subscribe(self, listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Independent data-only lifecycle hints, unaffected by a UI publish owner.

        Listeners may be called on the child loop and must marshal to their own
        owner. Removing a listener changes no execution/resource state.
        """
        key = object()
        with self._lock:
            if self._closed:
                raise RuntimeError("Child runtime closed")
            self._listeners[key] = listener
        def remove() -> None:
            with self._lock:
                self._listeners.pop(key, None)
        return remove

    def _notify(self, child: _Child) -> None:
        with self._lock:
            child.updated = time.time()
            if self._closed:
                return
            event = {"type": "child_status", "child_id": child.spec.id, "snapshot": self.snapshot()}
        self._dispatch(event)

    def _dispatch(self, event: dict[str, Any]) -> None:
        with self._lock:
            if self._closed:
                return
            targets = (*self._listeners.values(), self.publish)
        for target in targets:
            try:
                target(copy.deepcopy(event))
            except Exception:
                # A disconnected UI/listener cannot strand execution or disable
                # another service's lifecycle hint. Facts remain queryable.
                pass

    def _message_fact(self, child: _Child, event: AgentEvent, *, message_id: str,
                      direction: str, message: UserMessage | AssistantMessage, source: str, status: str) -> None:
        """Publish canonical admission/completion only; never a delivery or permission ACK.

        Initial input uses its existing recovery identity; queued inputs keep their
        original IDs; assistant IDs bind the original Agent event identity. No
        query, listener attachment, partial update or tool result creates a fact.
        """
        with self._lock:
            if message_id in child.message_ids:
                return
            child.message_ids.add(message_id)
        content = message.content
        text = content if isinstance(content, str) else "\n".join(
            part.text for part in content if isinstance(part, TextContent))
        fact = {"type": "child_message", "child_id": child.spec.id, "message_id": message_id,
                "direction": direction, "text": text, "status": status, "source": source,
                "run_id": event.run_id, "sequence": event.sequence,
                "image_count": (0 if isinstance(content, str) else
                                sum(isinstance(part, ImageContent) for part in content))}
        if isinstance(message, AssistantMessage):
            fact["stop_reason"] = message.stop_reason
        self._dispatch(fact)

    @staticmethod
    def _updated_prompt(child: _Child, agent: Agent) -> SystemPrompt:
        from xdog.agent.child_model import ChildModel
        model = ChildModel(id=agent.state.model, options=agent.options, context_window=agent.context_window,
                           max_prompt_tokens=agent.max_prompt_tokens, supports_tool_calls=agent.supports_tool_calls)
        return model.prompt(child_prompt("", child.spec))

    def _capture(self, child: _Child) -> None:
        assert child.agent is not None
        transcript = child.agent.messages
        try:
            canonical = image_parts(transcript)
            missing = tuple(part for part in child.spec.images if part not in set(canonical))
            check_collection((*canonical, *missing))
            check_collection(part for row in child.agent.pending_inputs() for part in row.images)
            check_retained((*canonical, *child.spec.images, *child.live_returns.retained_images(),
                            *(part for row in child.returned_backups for part in row.images),
                            *(part for row in child.agent.pending_inputs() for part in row.images)))
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        # Bound each protocol message, not the lifetime of a tool-calling task.
        # Never drop a prefix here: tool-call/result pairing belongs to Agent.
        for message in transcript:
            if _serialized_size((message,)) > MAX_TRANSCRIPT_BYTES:
                raise RuntimeError("Child single-message budget exhausted")
        with self._lock:
            if child.transcript != transcript:
                child.revision += 1
            child.transcript = transcript

    def _tool_source_id(self, child: _Child, call_id: str) -> str:
        for index in range(len(child.transcript) - 1, -1, -1):
            message = child.transcript[index]
            if isinstance(message, AssistantMessage):
                for number, part in enumerate(message.content):
                    if isinstance(part, ToolCall) and part.id == call_id:
                        return f"m{index}:p{number}"
        raise ValueError("Progress has no canonical child tool call")

    def _tool_progress(self, child: _Child, event: ToolExecutionUpdateEvent) -> None:
        if event.partial_result is None:
            return
        text = "\n".join(part.text for part in event.partial_result.content if isinstance(part, TextContent))
        images = sum(not isinstance(part, TextContent) for part in event.partial_result.content)
        if images:
            text += f"\n[Non-text progress parts: {images}; binary bodies omitted]"
        with self._lock:
            try:
                source_id = self._tool_source_id(child, event.tool_call_id)
            except ValueError:
                return  # A budget-vetoed/unretained tool batch has no display owner.
            previous = child.progress.get(source_id)
            if previous is None and len(child.progress) >= 128:
                if not child.progress_sources_limited:
                    child.progress_sources_limited = True
                    child.revision += 1
                return  # Canonical final results remain available; capacity loss is explicitly marked.
            used = sum(len(json.dumps(row.to_dict()).encode("utf-8"))
                       for key, row in child.progress.items() if key != source_id)
            overhead = len(json.dumps(ChildToolProgress(source_id, event.tool_name[:128], "", True).to_dict())
                           .encode("utf-8"))
            available = MAX_TRANSCRIPT_BYTES - used - overhead - 512
            if available < 2:
                if not child.progress_sources_limited:
                    child.progress_sources_limited = True
                    child.revision += 1
                return  # No room for this source's metadata; report omission rather than exceed the budget.
            # Transient display retention is NOT an execution veto. Bound the
            # escaped JSON bytes, mark clipping explicitly and keep final results authoritative.
            limited = len(json.dumps(text).encode("utf-8")) > available
            if limited:
                lower, upper = 0, len(text)
                while lower < upper:
                    middle = (lower + upper + 1) // 2
                    if len(json.dumps(text[:middle]).encode("utf-8")) <= available:
                        lower = middle
                    else:
                        upper = middle - 1
                text = text[:lower]
            row = ChildToolProgress(source_id, event.tool_name[:128], text, limited)
            if previous != row:
                child.progress[source_id] = row
                child.revision += 1

    async def _run(self, child: _Child, request: QueuedInput | None = None, *, scope_ready: bool = False) -> None:
        status, error = "failed", ""
        budget_error = ""
        compaction_source: tuple[AgentMessage, ...] | None = None
        def unsubscribe() -> None:
            pass
        def publish(event: dict[str, Any]) -> None:
            if not self._closed:
                try:
                    self.publish({**event, "child_id": child.spec.id})
                except Exception:
                    pass
        try:
            if request is not None and not scope_ready:
                captured = self.capture(child.spec)
                if inspect.isawaitable(captured):
                    captured = await captured
                if isinstance(captured, ChildSpec):
                    child.spec = captured
            beginning = asyncio.create_task(asyncio.to_thread(self.begin, child.spec)) if request is None else None
            if beginning is not None:
                try:
                    await asyncio.shield(beginning)
                except asyncio.CancelledError:
                    while not beginning.done():
                        try:
                            await asyncio.shield(beginning)
                        except asyncio.CancelledError:
                            continue
                    beginning.result()
                    raise
            built = self.factory(child.spec, publish)
            agent = await built if inspect.isawaitable(built) else built
            if agent.is_streaming:
                raise ValueError("Child factory must return a fresh idle Agent")
            # Never allow a factory to smuggle parent history into a new child.
            # Validate first so malformed historical tool pairs retain precise errors.
            validate_checkpoint(agent.messages, len(agent.messages) - 1)
            for message in agent.messages:
                _serialized_size((message,))
            if agent.messages:
                raise ValueError("Child factory must return an empty history; parent context is never inherited")
            child.agent = agent
            if request is not None:
                # Runtime clients, tools, authority and current model metadata are injected by the host.
                if child.session_state is None:
                    # Explicit bounded compatibility: no unknown skill/compaction state is invented.
                    agent.replace_messages(list(child.transcript))
                else:
                    agent.restore_parts(child.session_state, child.transcript, allowed_models=(agent.state.model,))
                agent.set_system_prompt(self._updated_prompt(child, agent))
            else:
                if isinstance(agent.stream_fn, ModelStreamFn):
                    from xdog.agent.async_work import run_owned
                    metadata = await run_owned(agent.stream_fn.resolve_model, agent.state.model)
                    if metadata is not None:
                        child.spec = replace(child.spec, model=agent.state.model,
                                             thinking=agent.options.thinking or "off")
                with self._lock:
                    child.tool_schemas = {tool.name: copy.deepcopy(tool.parameters) for tool in agent.state.tools
                                          if child.spec.tools is None or tool.name in child.spec.tools}
            if child.prompt_changed:
                agent.set_system_prompt(self._updated_prompt(child, agent))
            if self.persist_admission is not None:
                previous_checkpoint = agent._input_checkpoint
                async def checkpoint(event: AgentEvent) -> None:
                    if previous_checkpoint is not None:
                        await previous_checkpoint(event)
                    while child.queue_work:
                        await asyncio.sleep(0.005)
                    child.queue_work = True
                    from xdog.agent.async_work import run_owned
                    try:
                        self._capture(child)
                        record = self._record(child, transcript=True)
                        record.update(status='interrupted', settling=False,
                                      error='Owner interrupted; inspect history/effects; never automatically replayed.')
                        history = agent.messages
                        if isinstance(event, MessageEndEvent) and isinstance(event.message, UserMessage):
                            history = (*history, event.message)
                            if child.initial_state == 'pending':
                                record['initial_state'] = 'admitted'
                        elif isinstance(event, InputHandledEvent):
                            feedback = (*child.input_feedback, event.feedback)
                            if len(feedback) > 64 or sum(len(value.encode()) for value in feedback) > 65536:
                                raise RuntimeError('Child input feedback exceeds its retention limit')
                            record['input_feedback'] = list(feedback)
                            if child.initial_state == 'pending' and event.source == 'prompt':
                                record['initial_state'] = 'handled'
                        for message in history:
                            if _serialized_size((message,)) > MAX_TRANSCRIPT_BYTES:
                                raise RuntimeError('Child single-message budget exhausted')
                        canonical = image_parts(history)
                        missing = tuple(part for part in child.spec.images if part not in set(canonical))
                        check_collection((*canonical, *missing))
                        record['messages'] = (encode_snapshot_messages(history, interrupted=True)
                            if child.session_state is not None else messages_to_dicts(list(history)))
                        points = history_points(history)
                        record['checkpoint'] = points[-1].index if points else -1
                        persist_admission = self.persist_admission
                        assert persist_admission is not None
                        await run_owned(lambda: persist_admission(record))
                    except BaseException:
                        # Callback effects or rename may already have happened.
                        # Terminal cleanup must not downgrade that recovery to
                        # confidently pending just because no receipt was sent.
                        child.queue_uncertain = True
                        if child.initial_state == 'pending':
                            child.initial_state = 'uncertain'
                        raise
                    finally:
                        child.queue_work = False
                agent.set_input_checkpoint(checkpoint)
            if self.persist_inputs is not None:
                previous_settled = agent._input_settled
                async def persist_settled(rows: tuple[QueuedInput, ...]) -> None:
                    if previous_settled is not None:
                        await previous_settled(rows)
                    while child.queue_work:
                        await asyncio.sleep(0.005)
                    child.queue_work = True
                    from xdog.agent.async_work import run_owned
                    try:
                        # Capture after any prior journal writer: it may have
                        # admitted another input while this checkpoint waited.
                        pending = tuple(replace(row, selected=False) for row in agent.pending_inputs())
                        persist = self.persist_inputs
                        assert persist is not None
                        await run_owned(lambda: persist(child.spec.id, pending))
                    finally:
                        child.queue_work = False
                agent.set_input_settled(persist_settled)
            with self._lock:
                child.effective = tuple({"model": agent.state.model, "thinking": agent.options.thinking or "off",
                    "context_window": agent.context_window, "max_prompt_tokens": agent.max_prompt_tokens,
                    "max_output_tokens": agent.options.max_tokens,
                    "supports_tool_calls": agent.supports_tool_calls}.items())
            if child.spec.tools is not None:
                allowed = set(child.spec.tools)
                available = {tool.name for tool in agent.state.tools}
                if allowed - available:
                    raise ValueError("Child tool selection is unavailable: " + ", ".join(sorted(allowed - available)))
                from xdog.agent.tools import ToolRegistry
                selection = ToolRegistry()
                selection.replace_source("child", (tool for tool in agent.local_tools if tool.name in allowed))
                if agent.mcp_registry is not None:
                    agent.mcp_registry.replace_delegated_tools(
                        tool for tool in agent.mcp_registry.tools() if tool.name in allowed)
                    agent.sync_mcp_tools()
                selection.publish(agent)
            validate_checkpoint(agent.messages, len(agent.messages) - 1)
            if any(tool.name in RESERVED_TOOLS for tool in child.agent.state.tools):
                raise ValueError("Child tools cannot supervise children or mutate a parent plan")
            def observe(event: AgentEvent) -> None:
                nonlocal budget_error, compaction_source
                try:
                    if isinstance(event, CompactionStartEvent):
                        self._capture(child)
                        compaction_source = agent.messages
                    elif isinstance(event, (CompactionEndEvent, CompactionFailedEvent)):
                        compaction_source = None
                        self._capture(child)
                    if isinstance(event, UsageEvent) and event.source == "compaction":
                        with self._lock:
                            child.tokens += event.usage.total_tokens or event.usage.input + event.usage.output
                    if isinstance(event, (MessageEndEvent, InputConsumedEvent)):
                        self._capture(child)
                    if isinstance(event, InputConsumedEvent):
                        # Agent appends the actual (possibly transformed) user message
                        # before emitting consumption, preserving the original queue ID.
                        message = agent.messages[-1]
                        if isinstance(message, UserMessage):
                            self._message_fact(child, event, message_id=event.input.id,
                                direction="parent_to_child", message=message, source=event.input.delivery,
                                status="admitted")
                    if request is not None and (
                            isinstance(event, MessageEndEvent) and isinstance(event.message, UserMessage)
                            or isinstance(event, InputHandledEvent) and event.source == "prompt"):
                        with self._lock:
                            child.undelivered = tuple(row for row in child.undelivered if row.id != request.id)
                    if isinstance(event, (InputHandledEvent, InputRejectedEvent)) and event.source == 'prompt':
                        child.initial_message_observed = True
                    if isinstance(event, MessageEndEvent) and isinstance(event.message, UserMessage):
                        if not child.initial_message_observed:
                            child.initial_message_observed = True
                            self._message_fact(child, event,
                                message_id="initial-task:" + child.spec.id if request is None else request.id,
                                direction="parent_to_child", message=event.message,
                                source="prompt" if request is None else request.delivery, status="admitted")
                    if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
                        message = event.message
                        # An assistant tool-call-only message is not a textual reply.
                        if any(isinstance(part, TextContent) and part.text for part in message.content):
                            self._message_fact(child, event,
                                message_id=f"assistant:{event.run_id}:{event.sequence}", direction="child_to_parent",
                                message=message, source="assistant",
                                status=child_message_status(message))
                    if (child.initial_state == 'pending' and isinstance(event, MessageEndEvent)
                            and isinstance(event.message, UserMessage)):
                        child.initial_state = 'admitted'
                    if isinstance(event, InputHandledEvent):
                        if child.initial_state == 'pending' and event.source == 'prompt':
                            child.initial_state = 'handled'
                        with self._lock:
                            feedback = (*child.input_feedback, event.feedback)
                            if len(feedback) > 64 or sum(len(value.encode()) for value in feedback) > 65536:
                                raise RuntimeError("Child input feedback exceeds its retention limit")
                            child.input_feedback = feedback
                            child.result = "[Extension input handled; no model response]\n" + event.feedback
                            child.revision += 1
                    if isinstance(event, MessageUpdateEvent) and event.message is not None:
                        size = _serialized_size((event.message,))
                        if size > MAX_TRANSCRIPT_BYTES:
                            raise RuntimeError("Child streaming response budget exhausted")
                        with self._lock:
                            child.partial = event.message
                            child.revision += 1
                    if isinstance(event, ToolExecutionUpdateEvent):
                        self._tool_progress(child, event)
                    elif isinstance(event, ToolExecutionEndEvent):
                        with self._lock:
                            try:
                                source_id = self._tool_source_id(child, event.tool_call_id)
                            except ValueError:
                                source_id = ""
                            if child.progress.pop(source_id, None) is not None:
                                child.revision += 1
                    if isinstance(event, MessageEndEvent):
                        with self._lock:
                            child.partial = None
                        self._capture(child)
                        if isinstance(event.message, AssistantMessage):
                            text = "\n".join(part.text for part in event.message.content
                                             if isinstance(part, TextContent))
                            with self._lock:
                                child.tokens += (event.message.usage.total_tokens or
                                                 event.message.usage.input + event.message.usage.output)
                                if text:
                                    child.result = text
                except RuntimeError as exc:
                    budget_error = str(exc)
                    agent.abort()
            if request is not None:
                child.initial_message_observed = False
            unsubscribe = agent.subscribe(observe)
            original = child.agent.stream_fn
            def counted(model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
                nonlocal budget_error
                self._capture(child)
                if budget_error:
                    raise RuntimeError(budget_error)
                def start_request() -> EventStream[AssistantMessage]:
                    with self._lock:
                        child.calls += 1
                    return original(model, context, options)
                if compaction_source is not None:
                    if self.archive_compaction is None:
                        budget_error = "Child compaction requires an archive_compaction callback"
                        raise RuntimeError(budget_error)
                    source = compaction_source
                    generation = agent._compaction_state.generation + 1
                    async def archive() -> None:
                        from xdog.agent.async_work import run_owned
                        callback = self.archive_compaction
                        if callback is None:
                            raise RuntimeError("Child compaction requires an archive_compaction callback")
                        # The immutable snapshot is exported before the native
                        # summary can start, so neither a failed summary nor a
                        # receiver crash can erase the uncompacted tool pairs.
                        await run_owned(callback, child.spec, generation, source)
                    return ArchivedSummaryStream(start_request, archive)
                return start_request()
            child.agent.set_stream_fn(preserve_model_metadata(counted, original))
            self._capture(child)
            with self._lock:
                child.status = "running"
            self._notify(child)
            prompt = child_task(child.spec) if request is None else request.content
            images = child.spec.images if request is None else request.images
            stream = await child.agent.prompt(prompt, images=list(images))
            async for event in stream:
                if self.on_event is not None:
                    await self.on_event(child.spec, event)
                if budget_error:
                    raise RuntimeError(budget_error)
            if budget_error:
                raise RuntimeError(budget_error)
            error = child.agent.state.error or ""
            final = child.agent.messages[-1] if child.agent.messages else None
            provider_aborted = isinstance(final, AssistantMessage) and final.stop_reason == "aborted"
            status = ("canceled" if child.agent.cancellation_requested or provider_aborted
                      else "failed" if error else "completed")
        except asyncio.CancelledError:
            status = "canceled"
        except Exception as exc:
            status, error = "failed", str(exc)[:4000]
        finally:
            # Cleanup/persistence owns the slot even if a control is repeatedly canceled.
            cleanup = asyncio.create_task(self._settle(child, status, error))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
            unsubscribe()

    async def _settle(self, child: _Child, status: str, error: str) -> None:
        if child.agent is not None:
            await child.agent.cancel_and_wait()
            await child.agent.wait_for_idle()
            try:
                self._capture(child)
            except RuntimeError as exc:
                status, error = "failed", str(exc)
        while child.queue_work:
            await asyncio.sleep(0.005)  # Admitted input journal writers settle before terminal publication.
        cleanup_errors: list[str] = []
        if child.agent is not None:
            retained = {row.id: row for row in child.undelivered}
            retained.update((row.id, replace(row, selected=False)) for row in child.agent.drain_inputs())
            child.undelivered = tuple(retained.values())
            if status == "completed":
                try:
                    from xdog.agent.async_work import run_owned
                    snapshot_data = await run_owned(child.agent.dump_metadata)
                    child.session_state = snapshot_data
                except Exception as exc:
                    status, error = "failed", "Child continuation snapshot failed: " + type(exc).__name__
            # Agent close clears queues. Preserve the original pending inputs in
            # the host record first, then permanently fence even a factory-held
            # Agent reference before releasing its extension/resource owners.
            try:
                await child.agent.aclose()
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        if self.finalize is not None:
            try:
                await self.finalize(child.spec)
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        if cleanup_errors:
            status, error = "failed", "Child resource cleanup failed: " + ", ".join(cleanup_errors)
        with self._lock:
            child.status, child.error = status, error
            child.partial = None
            child.progress_sources_limited = False
            child.progress.clear()  # Live progress never substitutes for durable final tool results.
            child.agent = None  # Never retain oversized canonical provider/tool history after settlement.
            child.live_returns.clear()  # No stopped child retains process-local return leases/byte references.
        try:
            record = self._record(child, transcript=True)
            record["settling"] = False
            await asyncio.to_thread(self.persist, record)
        except Exception as exc:
            with self._lock:
                child.status, child.error = "failed", "Child persistence failed: " + type(exc).__name__
        finally:
            with self._lock:
                child.settling = False
            self._notify(child)

    async def control(self, action: str, identifier: str, text: str = "", *,
                      images: tuple[ImageContent, ...] = (), return_owner: str = '',
                      return_alive: Callable[[], bool] = lambda: True) -> dict[str, Any]:
        await asyncio.to_thread(validate_images, images)
        if images and action not in {"steer", "follow-up"}:
            raise ValueError("Images belong only to child steering/follow-up inputs")
        if (not isinstance(action, str) or action not in {"inspect", "steer", "follow-up",
                                                         "cancel", "wait", "close", "queue", "set_system_prompt"}
                or not isinstance(identifier, str) or not identifier):
            raise ValueError("Invalid child control")
        if action == "set_system_prompt":
            ChildConfig.from_dict({"system_prompt": text})
        receipt = await asyncio.to_thread(input_receipt, text, images)
        return await self._bridge(self._control(action, identifier, text, images, receipt, return_owner, return_alive))

    async def _admit_identified_input(self, identifier: str, agent: Agent, input_id: str, text: str,
                                      delivery: Delivery, images: tuple[ImageContent, ...],
                                      alive: Callable[[], bool]) -> QueuedInput:
        """Internal extension admission: original Agent + host ID; not external ID authority."""
        if (not isinstance(input_id, str) or re.fullmatch(r'extq-[a-f0-9]{32}', input_id) is None
                or delivery not in ('steer', 'follow_up') or not callable(alive)):
            raise ValueError('Invalid identified child input admission')
        await asyncio.to_thread(validate_images, images)
        async def admit() -> QueuedInput:
            child = self._children.get(identifier)
            if child is None or child.agent is not agent:
                raise ValueError('Original child Agent is unavailable')
            return await self._queue_admission(child, agent,
                                               QueuedInput(input_id, text, delivery, images=images), alive)
        return await self._bridge(admit())

    async def _discard_identified_input(self, identifier: str, agent: Agent, input_id: str) -> bool:
        """Remove only this host-generated unconsumed ID through canonical journaling."""
        if not isinstance(input_id, str) or re.fullmatch(r'extq-[a-f0-9]{32}', input_id) is None:
            return False
        async def discard() -> bool:
            from xdog.agent.child_queue import snapshot
            child = self._children.get(identifier)
            if child is None or child.agent is not agent or child.status != 'running':
                return False
            before = snapshot(agent, identifier, child.status)
            if not any(row['id'] == input_id and not row['selected'] for row in before['inputs']):
                return False
            if child.queue_work:
                agent.abort()  # Another journal must settle before canonical recovery can own this input.
                return False
            try:
                await self._control('queue', identifier, json.dumps({'action': 'remove', 'id': input_id,
                    'revision': before['revision']}), (), {}, '', lambda: True)
            except BaseException:
                agent.abort()  # Failed durable removal must not continue into invisible model consumption.
                raise
            return True
        return await self._bridge(discard())

    async def _queue_admission(self, child: _Child, agent: Agent, item: QueuedInput,
                               alive: Callable[[], bool]) -> QueuedInput:
        """Shared old-control/extension admission, one write-ahead/queue-work implementation."""
        if child.queue_work:
            raise RuntimeError('Child recovery storage still owns work; wait before changing it')
        if (not isinstance(item.content, str) or not (item.content.strip() or item.images)
                or len(item.content.encode('utf-8')) > 16000 or self._closed or not alive()
                or child.agent is not agent or child.status != 'running' or not agent.is_streaming
                or len(agent.pending_inputs()) >= 32 or item.id in agent._identified_inputs):
            raise ValueError('Child is not running or its bounded input queue is full/changed')
        check_collection((*item.images, *(part for row in agent.pending_inputs() for part in row.images)))
        check_retained((*image_parts(agent.messages), *child.spec.images, *item.images,
                        *child.live_returns.retained_images(),
                        *(part for row in child.returned_backups for part in row.images),
                        *(part for row in agent.pending_inputs() for part in row.images)))
        if self.persist_inputs is not None:
            # Persist BEFORE live acceptance. Cancellation must settle the actual
            # writer; snapshots are conservative recovery, never permission to replay.
            rows = (*tuple(replace(row, selected=False) for row in agent.pending_inputs()), item)
            child.queue_work = True
            writing = asyncio.create_task(asyncio.to_thread(self.persist_inputs, child.spec.id, rows))
            canceled = False
            try:
                while not writing.done():
                    try:
                        await asyncio.shield(writing)
                    except asyncio.CancelledError:
                        canceled = True
                writing.result()
                if canceled:
                    raise asyncio.CancelledError
                if (self._closed or not alive() or child.agent is not agent
                        or child.status != 'running' or not agent.is_streaming):
                    raise ValueError('Child stopped/revoked during write-ahead admission; uncertain, no enqueue')
                return agent.queue_input(item.id, item.content, item.delivery, item.images)
            finally:
                child.queue_work = False
        return agent.queue_input(item.id, item.content, item.delivery, item.images)

    @staticmethod
    def _initial_input(child: _Child) -> QueuedInput | None:
        if child.initial_state not in {'pending', 'uncertain'} or child.status not in {
                'failed', 'canceled', 'timed-out', 'interrupted'}:
            return None
        return QueuedInput('initial-task:' + child.spec.id, child_task(child.spec), 'follow_up',
                           images=child.spec.images)

    @classmethod
    def _stopped_queue(cls, child: _Child) -> dict[str, Any]:
        from xdog.agent.child_queue import snapshot
        return snapshot(None, child.spec.id, child.status, child.undelivered,
                        initial=cls._initial_input(child), initial_uncertain=child.initial_state == 'uncertain',
                        recovery_uncertain=child.queue_uncertain, returned=child.returned_backups)

    def recovery_current(self, identifier: str, revision: str) -> bool:
        with self._lock:
            child = self._children.get(identifier)
            return (not self._closed and child is not None and child.agent is None and not child.settling
                    and not child.queue_work
                    and self._stopped_queue(child)['revision'] == revision)

    async def return_current(self, identifier: str, token: str, owner: str) -> bool:
        async def current() -> bool:
            child = self._children.get(identifier)
            return (not self._closed and child is not None
                    and child.live_returns.current(child.agent, identifier, child.status, token, owner))
        return await self._bridge(current())

    async def _control(self, action: str, identifier: str, text: str,
                       images: tuple[ImageContent, ...], receipt: dict[str, Any], return_owner: str,
                       return_alive: Callable[[], bool]) -> dict[str, Any]:
        child = self._children.get(identifier)
        if child is None:
            raise ValueError("Child is unavailable or belongs to another child runtime")
        # Live cancellation/close must remain reachable: _settle awaits its writer.
        # A stopped recovery writer instead owns the durable record itself; close
        # must not archive/remove that record while its reviewed update commits.
        if child.queue_work and (action in {'queue', 'steer', 'follow-up', 'set_system_prompt'}
                                 or action == 'close' and child.agent is None):
            raise RuntimeError('Child recovery storage still owns work; wait before changing it')
        if action == "set_system_prompt":
            # Agent's run and compaction capture system prompt snapshots. Do not
            # mutate either mid-run, restart the model, or replay user/tool history.
            if child.status not in {"queued", "completed", "failed", "canceled", "timed-out", "interrupted"}:
                raise RuntimeError("set_system_prompt is idle-only; child is busy, no prompt changed")
            if ((child.agent is not None and child.agent.is_streaming)
                    or child.status != "queued" and child.settling):
                raise RuntimeError("set_system_prompt is idle-only; child is still settling")
            spec = replace(child.spec, system_prompt=text)
            if child.status != "queued":
                record = self._record(child, transcript=True)
                record["config"] = spec.config().to_dict()
                child.queue_work, child.settling = True, True
                writing = asyncio.create_task(asyncio.to_thread(self.persist, record))
                canceled = False
                try:
                    while not writing.done():
                        try:
                            await asyncio.shield(writing)
                        except asyncio.CancelledError:
                            canceled = True
                    writing.result()
                    child.spec = spec
                finally:
                    child.queue_work, child.settling = False, False
                if canceled:
                    raise asyncio.CancelledError
            else:
                child.spec = spec
                child.prompt_changed = True
                if child.agent is not None:
                    child.agent.set_system_prompt(self._updated_prompt(child, child.agent))
            self._notify(child)
            return self._record(child, transcript=True)
        if action == "queue":
            from xdog.agent.child_queue import change, recovery_to_dicts, request
            try:
                command = json.loads(text)
            except (ValueError, TypeError) as exc:
                raise ValueError("Child queue control requires a typed JSON request") from exc
            command = request(command)
            if command['action'] in {'return_prepare', 'return_commit', 'return_cancel'}:
                if not return_alive():
                    raise ValueError('Live image return host owner revoked before admission; no removal')
                if command['action'] == 'return_prepare':
                    agent = child.agent
                    if agent is None or child.status != 'running':
                        raise ValueError('Live image return requires running child; use stopped recovery')
                    result = await child.live_returns.prepare(agent, identifier, child.status, command, return_owner)
                    if child.agent is not agent or child.status != 'running' or not return_alive():
                        child.live_returns.cancel({**command, 'token': result['queue']['return_token']}, return_owner)
                        raise ValueError('Child stopped during return preparation; no removal')
                    return result
                if command['action'] == 'return_cancel':
                    child.live_returns.cancel(command, return_owner)
                    from xdog.agent.child_queue import snapshot
                    return {'queue': snapshot(child.agent, identifier, child.status, child.undelivered)}
                agent = child.agent
                rows = child.live_returns.planned_commit(agent, identifier, child.status, command, return_owner)
                if self.persist_inputs is not None and rows is not None:
                    backup = None
                    if self.persist_return is not None:
                        assert agent is not None
                        original = next(row for row in agent.pending_inputs() if row.id == command['id'])
                        backup = replace(original, id='returned:' + command['token'], selected=False)
                        from xdog.agent.child_queue import recovery_from_dicts, recovery_to_dicts
                        existing = next((row for row in child.returned_backups if row.id == backup.id), None)
                        if existing is not None and existing != backup:
                            raise ValueError('Retained return backup identity changed; no replay')
                        prospective = (child.returned_backups if existing is not None
                                       else (*child.returned_backups, backup))
                        recovery_from_dicts(recovery_to_dicts(prospective))
                    child.queue_work = True
                    backup_written = threading.Event()
                    def persist_commit() -> None:
                        if backup is not None and self.persist_return is not None:
                            try:
                                self.persist_return(identifier, backup)
                            finally:
                                # A failed flush may follow a committed rename.
                                # Retain the conservative exact copy on settlement
                                # even when backup admission itself is uncertain.
                                backup_written.set()
                        assert self.persist_inputs is not None and rows is not None
                        self.persist_inputs(identifier, rows)
                    writing = asyncio.create_task(asyncio.to_thread(persist_commit))
                    canceled = False
                    try:
                        while not writing.done():
                            try:
                                await asyncio.shield(writing)
                            except asyncio.CancelledError:
                                canceled = True
                        writing.result()
                        if canceled:
                            raise asyncio.CancelledError
                        if child.agent is not agent or not return_alive():
                            raise ValueError('Return owner changed during journal write; inspect, no replay')
                        # The lease can expire or the checkpoint can select the
                        # input during the write. Commit rechecks before removal.
                        result = child.live_returns.commit(agent, identifier, child.status, command, return_owner)
                    finally:
                        if backup is not None and backup_written.is_set() and not any(
                                row.id == backup.id for row in child.returned_backups):
                            child.returned_backups = (*child.returned_backups, backup)
                        child.queue_work = False
                else:
                    result = child.live_returns.commit(agent, identifier, child.status, command, return_owner)
                self._notify(child)
                return result
            if child.agent is None:
                result = self._stopped_queue(child)
                if command['action'] == 'list':
                    return {'queue': result}
                if child.settling or command['revision'] != result['revision']:
                    raise ValueError('Stopped child recovery changed/still settling; refresh, no replay')
                item = next((row for row in result['inputs'] if row['id'] == command['id']), None)
                if item is None:
                    raise ValueError('Undelivered child input no longer available')
                if item.get('source') == 'initial-task':
                    initial = self._initial_input(child)
                    if initial is None:
                        raise ValueError('Initial task no longer available')
                    if command['action'] == 'recover_task':
                        if initial.images:
                            from xdog.agent.child_transfer import prepare_recovery
                            prepared = await prepare_recovery(initial)
                            if not self.recovery_current(identifier, command['revision']):
                                raise ValueError('Initial task changed during preparation; no replay')
                            return {'queue': {**result, 'recovered': item, 'image_receipt': prepared['receipt']},
                                    'image_recovery': prepared}
                        return {'queue': {**result, 'recovered': item}}
                    if command['action'] != 'remove_task':
                        raise ValueError('Initial task requires explicit task copy/discard, not queue mutation')
                    record = self._record(child, transcript=True)
                    record['initial_state'] = 'discarded'
                    record.pop('task_images', None)
                    record['settling'] = False
                    child.queue_work, child.settling = True, True
                    writing = asyncio.create_task(asyncio.to_thread(self.persist, record))
                    canceled = False
                    try:
                        while not writing.done():
                            try:
                                await asyncio.shield(writing)
                            except asyncio.CancelledError:
                                canceled = True
                        writing.result()
                        child.initial_state = 'discarded'
                        child.spec = replace(child.spec, images=())
                    finally:
                        child.queue_work, child.settling = False, False
                    if canceled:
                        raise asyncio.CancelledError
                    return {'queue': self._stopped_queue(child)}
                if command['action'] in {'recover_task', 'remove_task'}:
                    raise ValueError('Task recovery action needs the initial-task source row')
                if command['action'] == 'recover_images':
                    from xdog.agent.child_transfer import prepare_recovery
                    row = next(row for row in (*child.undelivered, *child.returned_backups) if row.id == command['id'])
                    if not row.images:
                        raise ValueError('Use ordinary recovery for text-only child inputs')
                    prepared = await prepare_recovery(row)
                    if not self.recovery_current(identifier, command['revision']):
                        raise ValueError('Child image recovery changed during preparation; no copy or replay')
                    return {'queue': {**result, 'recovered': item, 'image_receipt': prepared['receipt']},
                            'image_recovery': prepared}
                if command['action'] == 'recover':
                    if item['image_count']:
                        raise ValueError('Stopped image recovery needs a verified binary receipt; '
                                         'image recovery cannot be lossy')
                    # Recovery is an explicit non-destructive copy. A lost/canceled
                    # receipt must not erase the last durable copy or restart work.
                    return {'queue': {**result, 'recovered': item}}
                if command['action'] != 'remove':
                    raise ValueError('Stopped child inputs can only be copied to a fresh draft or explicitly discarded')
                remaining = tuple(row for row in child.undelivered if row.id != command['id'])
                record = self._record(child, transcript=True)
                record['undelivered_inputs'] = recovery_to_dicts(remaining)
                backups = tuple(row for row in child.returned_backups if row.id != command['id'])
                record['returned_backups'] = recovery_to_dicts(backups)
                record['settling'] = False
                child.queue_work, child.settling = True, True
                writing = asyncio.create_task(asyncio.to_thread(self.persist, record))
                canceled = False
                try:
                    while not writing.done():
                        try:
                            await asyncio.shield(writing)
                        except asyncio.CancelledError:
                            canceled = True
                    writing.result()
                    child.undelivered = remaining  # Publish only the actually committed removal.
                    child.returned_backups = backups
                finally:
                    child.queue_work, child.settling = False, False
                if canceled:
                    raise asyncio.CancelledError
                result = self._stopped_queue(child)
            else:
                if command['action'] in {'recover', 'recover_images', 'recover_task', 'remove_task'}:
                    raise ValueError('Running child inputs use reviewed return, not stopped-input recovery')
                if self.persist_inputs is not None and command['action'] in {'edit', 'remove', 'move', 'return'}:
                    from xdog.agent.child_queue import planned_change
                    agent = child.agent
                    rows = planned_change(agent, identifier, child.status, command)
                    child.queue_work = True
                    writing = asyncio.create_task(asyncio.to_thread(self.persist_inputs, identifier, rows))
                    canceled = False
                    try:
                        while not writing.done():
                            try:
                                await asyncio.shield(writing)
                            except asyncio.CancelledError:
                                canceled = True
                        writing.result()
                        if canceled:
                            raise asyncio.CancelledError
                        if child.agent is not agent or child.status != 'running':
                            raise ValueError('Child stopped during write-ahead queue change; inspect, no replay')
                        # Revalidate the exact lease after filesystem work. Consumption
                        # may have advanced while writing; a stale plan cannot mutate it.
                        result = change(agent, identifier, child.status, command)
                    finally:
                        child.queue_work = False
                else:
                    result = change(child.agent, identifier, child.status, command)
            self._notify(child)
            return {"queue": result}
        if action == "inspect":
            return self._record(child, transcript=True)
        if action in {"steer", "follow-up"}:
            if child.agent is None:
                await self._continue(child, text, images, return_alive)
                result = self._record(child, transcript=True)
                result["accepted_input"] = receipt
                return result
            await self._queue_admission(child, child.agent,
                QueuedInput(uuid.uuid4().hex, text, 'steer' if action == 'steer' else 'follow_up', images=images),
                lambda: True)
        elif action in {"cancel", "close"}:
            with self._lock:
                if child.settling and child.status in {"queued", "running"}:
                    child.status = "canceling"  # Admission may own IO before an execution task exists.
            if child.settling and child.task is not None:
                if child.agent is not None:
                    child.agent.abort()
                # A queued child must enter its try/finally before it can be canceled.
                await asyncio.sleep(0)
                if not child.task.done() and not child.task.cancelling():
                    child.task.cancel()
                await self._join(child.task)
            if action == "close":
                with self._lock:
                    child.settling = True  # Archiving is still session-owned metadata work.
                writing = asyncio.create_task(asyncio.to_thread(self.forget, identifier))
                try:
                    await asyncio.shield(writing)
                except asyncio.CancelledError:
                    while not writing.done():
                        try:
                            await asyncio.shield(writing)
                        except asyncio.CancelledError:
                            continue
                    writing.result()
                    raise
                finally:
                    with self._lock:
                        child.settling = False
                        if writing.done() and not writing.cancelled() and writing.exception() is None:
                            self._children.pop(identifier, None)
        elif action == "wait":
            if child.task is not None:
                await asyncio.shield(child.task)
        else:
            raise ValueError("Unknown child control")
        self._notify(child)
        result = self._record(child, transcript=True)
        if action in {"steer", "follow-up"}:
            result['accepted_input'] = receipt
        return result

    @staticmethod
    def _legacy_readonly(child: _Child) -> tuple[ChildSpec, dict[str, dict[str, Any]]]:
        """Explicit legacy handoff: only proven completed history, with a narrowed read-only grant."""
        from xdog.agent.compaction import SUMMARY_TEMPLATE
        from xdog.ai.types import ToolResultMessage
        if (child.spec.access != "read-only" or child.spec.inherit_context or child.spec.skills
                or child.spec.skills is None or not child.transcript):
            raise ValueError("Legacy child has unknown authority/skill state; explicit reconciliation required")
        typed = decode_snapshot_messages(encode_snapshot_messages(child.transcript))
        last = typed[-1]
        if not isinstance(last, AssistantMessage) or last.stop_reason not in {"stop", "length"} or last.error_message:
            raise ValueError("Legacy child has no confirmed completion boundary; no automatic replay")
        prefix = SUMMARY_TEMPLATE.partition("{summary}")[0]
        for message in typed:
            if not isinstance(message, (UserMessage, AssistantMessage, ToolResultMessage)):
                raise ValueError("Legacy child has unknown context state; explicit reconciliation required")
            content = message.content
            plain = content if isinstance(content, str) else "".join(
                part.text for part in content if isinstance(part, TextContent))
            if isinstance(message, UserMessage) and plain.startswith(prefix):
                raise ValueError("Legacy compacted history lacks its summary metadata; "
                                 "explicit reconciliation required")
        effective = dict(child.effective)
        model = effective.get("model") or child.spec.model
        if not isinstance(model, str) or not model:
            raise ValueError("Legacy child lacks a verified model binding; no fallback")
        successful = {message.tool_call_id for message in typed
                      if isinstance(message, ToolResultMessage) and not message.is_error}
        ceilings: dict[str, dict[str, Any]] = {}
        actions: dict[str, set[str]] = {}
        original_actions = dict(child.spec.tool_actions)
        for message in typed:
            if not isinstance(message, AssistantMessage):
                continue
            for call in message.content:
                if not isinstance(call, ToolCall) or call.id not in successful:
                    continue
                action = call.arguments.get("action")
                if call.name == "filesystem":
                    if (action not in {"read", "ls", "grep", "find"}
                            or call.name in original_actions and action not in original_actions[call.name]):
                        continue
                    actions.setdefault(call.name, set()).add(action)
                elif call.name != "current_time" or call.name in original_actions:
                    continue
                if child.spec.tools is not None and call.name not in child.spec.tools:
                    continue
                schema = ceilings.setdefault(call.name, {"type": "object", "enum": []})
                if call.arguments not in schema["enum"]:
                    schema["enum"].append(copy.deepcopy(call.arguments))
        spec = replace(child.spec, tools=tuple(ceilings), skills=(), model=model,
                       tool_scopes={name: scope for name, scope in child.spec.tool_scopes.items() if name in ceilings},
                       thinking=effective.get("thinking", child.spec.thinking),
                       tool_actions=tuple((name, tuple(sorted(values))) for name, values in actions.items()))
        spec.config()  # All new settings still pass the SDK's normal data validation on capture.
        return spec, ceilings

    async def _continue(self, child: _Child, text: str, images: tuple[ImageContent, ...],
                        alive: Callable[[], bool]) -> None:
        from xdog.agent.async_work import run_owned
        owner_alive = alive()
        with self._lock:
            if self._closed or self._admission_pauses or not owner_alive:
                raise ValueError("Original child owner is closed/paused; continuation refused")
            if child.settling or child.queue_work:
                raise ValueError("Child is still settling; wait before sending a new message")
            if child.status != "completed" or child.queue_uncertain:
                raise ValueError("Child is not running or safely completed; "
                                 "inspect/reconcile interrupted effects, no replay")
            legacy = self._legacy_readonly(child) if child.session_state is None else None
            if child.session_state is not None:
                decode_snapshot_parts(child.session_state, child.transcript).require_resumable()
            if (not isinstance(text, str) or not (text.strip() or images)
                    or len(text.encode("utf-8")) > 16000):
                raise ValueError("Invalid child continuation input; expected nonempty text/images within 16000 bytes")
            request = QueuedInput(uuid.uuid4().hex, text, "follow_up", images=images)
            canonical = image_parts(child.transcript)
            check_collection((*canonical, *images))
            check_retained((*canonical, *(part for row in child.undelivered for part in row.images),
                            *(part for row in child.returned_backups for part in row.images), *images))
            from xdog.agent.child_queue import recovery_from_dicts, recovery_to_dicts
            pending = (*child.undelivered, request)
            encoded = recovery_to_dicts(pending)
            recovery_from_dicts(encoded)  # Validate the complete candidate before publishing flags or writing.
            record = self._record(child, transcript=True)
            record.update(status="interrupted", settling=False, queue_uncertain=True,
                          undelivered_inputs=encoded,
                          error="Continuation admission may be unconfirmed; inspect, never automatically replay.")
            if legacy is not None:
                spec, ceilings = legacy
                note = ("Explicit legacy read-only continuation: retained paired history; tools restricted to "
                        "previously successful read-only argument sets. "
                        "Unknown skill/compaction state is not inferred.")
                record.update(config=spec.config().to_dict(), tool_schemas=copy.deepcopy(ceilings),
                              continuation_note=note)
                child.spec, child.tool_schemas, child.continuation_note = spec, ceilings, note
            original_pending = child.undelivered
            child.undelivered = pending
            child.settling, child.queue_work = True, True
            child.status = "queued"
        scope_ready = False
        if child.spec.tool_scopes:
            # Scoped continuation is a fresh capability admission. Reject stale
            # scope/parent authority BEFORE committing even its input metadata.
            # queue_work fences competing continuations while factory IO settles.
            try:
                captured = self.capture(child.spec)
                if inspect.isawaitable(captured):
                    captured = await captured
                if isinstance(captured, ChildSpec):
                    child.spec = captured
                scope_ready = True
                record["config"] = child.spec.config().to_dict()
                with self._lock:
                    if self._closed or self._admission_pauses or not alive() or child.status != "queued":
                        raise ValueError("Original child owner changed during scope admission")
            except BaseException:
                try:
                    if self.finalize is not None:
                        from xdog.agent.async_work import wait_owned
                        await wait_owned(asyncio.ensure_future(self.finalize(child.spec)))
                finally:
                    with self._lock:
                        child.undelivered = original_pending
                        child.settling = child.queue_work = False
                        if child.status == "queued":
                            child.status = "completed"
                    self._notify(child)
                raise
        started = False
        try:
            await run_owned(self.persist, record)
            owner_alive = alive()
            with self._lock:
                if self._closed or not owner_alive or child.status != "queued":
                    raise ValueError("Original child owner retired/cancelled during continuation admission")
                child.status, child.error = "queued", ""
                child.task = asyncio.create_task(self._run(child, request, scope_ready=scope_ready))
                self._watch_task(child.task)
                started = True
        finally:
            try:
                if scope_ready and not started and self.finalize is not None:
                    from xdog.agent.async_work import wait_owned
                    await wait_owned(asyncio.ensure_future(self.finalize(child.spec)))
            finally:
                with self._lock:
                    child.queue_work = False
                    if not started:
                        child.status = "interrupted"
                        child.error = "Continuation admission uncertain; inspect, no replay"
                        child.queue_uncertain = True
                        child.settling = False
                self._notify(child)

    def _watch_task(self, task: asyncio.Task[None]) -> None:
        """Host-local completion hook; borrowed host loops remain owned by their application."""

    @staticmethod
    async def _join(task: asyncio.Task[None]) -> None:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        if not task.cancelled():
            task.result()

    async def _close_children(self) -> None:
        await asyncio.sleep(0)
        with self._lock:
            admissions = tuple(self._admissions)
        for operation in admissions:
            if not operation.done() and not operation.cancelling():
                operation.cancel()
        children = tuple(self._children.values())
        for child in children:
            if child.queue_work and child.status == "queued" and child.agent is None:
                child.status = "canceling"  # Fence a continuation whose durable admission is still writing.
            if child.agent is not None:
                child.agent.abort()
            if child.task is not None and not child.task.done() and not child.task.cancelling():
                child.task.cancel()
        from functools import partial

        from xdog.agent.async_work import settle_cleanup
        async def settle_child(child: _Child) -> None:
            async def join() -> None:
                if child.task is not None:
                    await self._join(child.task)
            async def metadata() -> None:
                while child.queue_work:
                    await asyncio.sleep(0.005)
            await settle_cleanup(join, metadata, message='Child cleanup failed')
        async def settle_admissions() -> None:
            # Join all actual admission owners even if one reports a rejection;
            # child execution/storage cleanup must still run afterwards.
            results = await asyncio.gather(*(self._join(operation) for operation in admissions),
                                           return_exceptions=True)
            errors = [result for result in results if isinstance(result, BaseException)]
            if errors:
                raise BaseExceptionGroup('Claw child admission cleanup failed', errors)
        await settle_cleanup(settle_admissions, *(partial(settle_child, child) for child in children),
                             message='Claw children cleanup failed')

    async def aclose(self) -> None:
        from xdog.agent.async_work import wait_owned
        self.close()
        future = self._close_future
        if future is None:
            return
        async def settle() -> None:
            if isinstance(future, concurrent.futures.Future):
                await asyncio.shield(asyncio.wrap_future(future))
            else:
                await settle_on(future.get_loop(), self._join(future))
        await wait_owned(asyncio.create_task(settle()))

    def _schedule_cleanup(self, loop: asyncio.AbstractEventLoop) -> None:
        try:
            same_loop = asyncio.get_running_loop() is loop
        except RuntimeError:
            same_loop = False
        if same_loop:
            self._close_future = loop.create_task(self._close_children())
        else:
            self._close_future = asyncio.run_coroutine_threadsafe(self._close_children(), loop)
        def consume(result: Any) -> None:
            if not result.cancelled():
                result.exception()  # aclose still propagates the original settlement error.
        self._close_future.add_done_callback(consume)

    def abort(self) -> None:
        """Cancel current work only; never stop the application's loop."""
        with self._start_lock:
            if self._closed:
                return
            self._tool_return_owner = uuid.uuid4().hex
            if self._loop is not None and not self._loop.is_closed():
                self._schedule_cleanup(self._loop)

    def close(self) -> None:
        """Fence new admission; the host keeps its loop alive through aclose."""
        with self._start_lock:
            if self._closed:
                return
            self._closed = True
            self._tool_return_owner = uuid.uuid4().hex
            if self._loop is not None and not self._loop.is_closed():
                self._schedule_cleanup(self._loop)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def tool_return_owner(self) -> str:
        return self._tool_return_owner

    def control_alive(self, owner: str) -> bool:
        return not self._closed and self._tool_return_owner == owner

    def tools(self) -> tuple[AgentTool, ...]:
        return ChildClient(self).tools()
