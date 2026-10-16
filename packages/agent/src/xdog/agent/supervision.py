"""Bounded, session-owned child agents; host factories remain the permission boundary.

This is runtime infrastructure, not an OS sandbox or a client permission policy.
Factories must create fresh agents, enforce access, and snapshot stable parent context.
Reported-token budgets are a stop threshold, not an exact billable-token guarantee:
provider input accounting and providers ignoring max_tokens can overshoot a request.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import math
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar

from xdog.agent.agent import Agent
from xdog.agent.core import AgentMessage, AgentTool, AgentToolResult
from xdog.agent.events import (
    AgentEvent,
    InputHandledEvent,
    MessageEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionUpdateEvent,
)
from xdog.agent.history import history_points, validate_checkpoint
from xdog.agent.input_queue import Delivery, QueuedInput
from xdog.agent.messages import dicts_to_messages, messages_to_dicts
from xdog.agent.supervision_config import ChildConfig, ChildPresets
from xdog.agent.supervision_images import (
    check_collection,
    check_retained,
    image_parts,
    images_from_dicts,
    images_to_dicts,
    input_receipt,
    text_size,
    validate_images,
)
from xdog.agent.supervision_return import LiveImageReturns
from xdog.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    StreamOptions,
    SystemPrompt,
    SystemPromptBlock,
    TextContent,
    ToolCall,
    UserMessage,
)
from xdog.ai.utils.event_stream import EventStream

T = TypeVar("T")
RESERVED_TOOLS = frozenset({"agent_spawn", "agent_control", "update_plan"})
MAX_TRANSCRIPT_BYTES = 512 * 1024


def _serialized_size(messages: tuple[AgentMessage, ...]) -> int:
    try:
        return text_size(messages)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Child transcript is not JSON serializable") from exc


async def settle_on(loop: asyncio.AbstractEventLoop, coroutine: Coroutine[Any, Any, T]) -> T:
    """Run on an existing host loop; cancellation waits for actual coroutine cleanup."""
    if loop is asyncio.get_running_loop():
        return await coroutine
    if loop.is_closed():
        coroutine.close()
        raise RuntimeError("Host service loop closed")
    result: concurrent.futures.Future[T] = concurrent.futures.Future()
    settled: concurrent.futures.Future[None] = concurrent.futures.Future()
    canceled = threading.Event()
    task_ref: list[asyncio.Task[T]] = []
    def start() -> None:
        if canceled.is_set():
            coroutine.close()
            result.cancel()
            settled.set_result(None)
            return
        task = loop.create_task(coroutine)
        task_ref.append(task)
        def done(completed: asyncio.Task[T]) -> None:
            if completed.cancelled():
                result.cancel()
            elif (error := completed.exception()) is not None:
                result.set_exception(error)
            else:
                result.set_result(completed.result())
            settled.set_result(None)
        task.add_done_callback(done)
    loop.call_soon_threadsafe(start)
    wrapped = asyncio.wrap_future(result)
    try:
        return await asyncio.shield(wrapped)
    except asyncio.CancelledError:
        canceled.set()
        def cancel() -> None:
            if task_ref and not task_ref[0].cancelling():
                task_ref[0].cancel()
        loop.call_soon_threadsafe(cancel)
        while not settled.done():
            try:
                await asyncio.shield(asyncio.wrap_future(settled))
            except asyncio.CancelledError:
                continue
        if wrapped.done() and not wrapped.cancelled():
            wrapped.exception()
        else:
            wrapped.cancel()
        raise



@dataclass(frozen=True, slots=True)
class ChildSpec:
    id: str
    task: str
    access: str = "read-only"
    timeout: float = 120
    max_model_calls: int = 16
    max_tokens: int = 32000
    inherit_context: bool = True
    label: str = ""
    instructions: str = ""
    tools: tuple[str, ...] | None = None
    model: str | None = None
    thinking: str | None = None
    preset: str | None = None
    images: tuple[ImageContent, ...] = ()

    def config(self) -> ChildConfig:
        return ChildConfig(self.label, self.instructions, self.tools, self.inherit_context,
                           self.timeout, self.max_model_calls, self.max_tokens, self.model, self.thinking, self.preset)


def child_prompt(prompt: SystemPrompt | None, spec: ChildSpec) -> SystemPrompt:
    note = (f"\n\nYou are supervised child {spec.id} with {spec.access} access. "
            "Return findings/results to your supervisor. You share the parent's workspace: "
            "coordinate conflicting writes. You cannot spawn descendants or update the parent plan. "
            "Inherited access never exceeds the parent's current tool/planning policy.")
    if spec.label:
        note += "\nSpecialist label: " + spec.label
    if spec.instructions:
        note += ("\nTask-specific instructions (host permission restrictions remain authoritative):\n"
                 + spec.instructions)
    if isinstance(prompt, str) or prompt is None:
        return (prompt or "") + note
    return (*prompt, SystemPromptBlock(text=note, cache=False))


@dataclass(frozen=True, slots=True)
class ChildToolProgress:
    source_id: str
    name: str
    text: str
    limited: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.source_id, "name": self.name, "text": self.text, "limited": self.limited}


@dataclass(frozen=True, slots=True)
class ChildCapture:
    id: str
    task: str
    access: str
    status: str
    calls: int
    tokens: int
    revision: int
    settling: bool
    error: str
    messages: tuple[AgentMessage, ...]
    partial: AgentMessage | None = None
    progress: tuple[ChildToolProgress, ...] = ()
    progress_sources_limited: bool = False
    config: ChildConfig = field(default_factory=ChildConfig)
    effective: tuple[tuple[str, Any], ...] = ()
    input_feedback: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "task": self.task, "access": self.access, "status": self.status,
            "calls": self.calls, "tokens": self.tokens, "revision": self.revision, "settling": self.settling,
            "error": self.error, "messages": messages_to_dicts(list(self.messages)), "config": self.config.to_dict()}
        if self.input_feedback:
            result["input_feedback"] = list(self.input_feedback)
        if self.effective:
            result["effective"] = dict(self.effective)
        if self.progress_sources_limited:
            result["live_tools_limited"] = True
        if self.progress:
            result["live_tools"] = [row.to_dict() for row in self.progress]
        if self.partial is not None:
            result["partial"] = messages_to_dicts([self.partial])[0]
        return result


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


class ChildSupervisor:
    def __init__(
        self,
        factory: Callable[[ChildSpec, Callable[[dict[str, Any]], None]], Agent | Awaitable[Agent]],
        persist: Callable[[dict[str, Any]], None] | None = None,
        *, capture: Callable[[ChildSpec], None] | None = None,
        begin: Callable[[ChildSpec], None] | None = None,
        persist_inputs: Callable[[str, tuple[QueuedInput, ...]], None] | None = None,
        persist_return: Callable[[str, QueuedInput], None] | None = None,
        persist_admission: Callable[[dict[str, Any]], None] | None = None,
        forget: Callable[[str], None] | None = None,
        finalize: Callable[[ChildSpec], Awaitable[None]] | None = None,
        on_event: Callable[[ChildSpec, AgentEvent], Awaitable[None]] | None = None,
        records: list[dict[str, Any]] | None = None,
        presets: ChildPresets | None = None,
    ) -> None:
        self.factory = factory
        self.presets = presets or ChildPresets()
        self.capture = capture or (lambda spec: None)
        self.begin = begin or (lambda spec: None)
        self.persist_inputs = persist_inputs
        self.persist_return = persist_return
        self.persist_admission = persist_admission
        self.forget = forget or (lambda identifier: None)
        self.on_event = on_event
        self.finalize = finalize
        self.persist = persist or (lambda record: None)
        self.diagnostic = ""
        self.publish: Callable[[dict[str, Any]], None] = lambda event: None
        self._children: dict[str, _Child] = {}
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._closed = False
        # Process-local main-Agent ownership, never an argument, result or durable record.
        # tools() is refreshed by hosts; the nonce must survive those refreshes.
        self._tool_return_owner = uuid.uuid4().hex
        self._admission_pauses = 0
        from xdog.agent.supervision_queue import recovery_from_dicts
        for record in records or ():
            config = ChildConfig.from_dict(record.get("config", {}))
            spec = ChildSpec(record["id"], record["task"], record["access"], config.timeout, config.max_model_calls,
                             config.max_tokens, config.inherit_context, config.label, config.instructions,
                             config.tools, config.model, config.thinking, config.preset,
                             images_from_dicts(record.get('task_images', [])))
            self._children[spec.id] = _Child(
                spec, status=record["status"], result=record.get("result", ""), error=record.get("error", ""),
                calls=record.get("calls", 0), tokens=record.get("tokens", 0), updated=record.get("updated", 0),
                transcript=tuple(dicts_to_messages(record.get("messages", []))), settling=False,
                effective=tuple(record.get("effective", {}).items()),
                input_feedback=tuple(record.get("input_feedback", ())),
                undelivered=recovery_from_dicts(record.get('undelivered_inputs', [])),
                queue_uncertain=record.get('queue_uncertain', False),
                returned_backups=recovery_from_dicts(record.get('returned_backups', [])),
                initial_state=record.get('initial_state', 'uncertain' if record.get('task_images')
                                        or not record.get('messages') else 'admitted'),
            )

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._start_lock:
            if self._closed:
                raise RuntimeError("Child supervisor closed")
            if self._thread is None:
                def run() -> None:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    self._loop = loop
                    self._ready.set()
                    try:
                        loop.run_forever()
                    finally:
                        tasks = asyncio.all_tasks(loop)
                        for task in tasks:
                            task.cancel()
                        if tasks:
                            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                        loop.run_until_complete(loop.shutdown_asyncgens())
                        loop.run_until_complete(loop.shutdown_default_executor())
                        loop.close()
                self._thread = threading.Thread(target=run, daemon=True, name="xdog-child-owner")
                self._thread.start()
            if not self._ready.wait(3) or self._loop is None:
                raise RuntimeError("Child owner loop did not start")
            return self._loop

    async def _bridge(self, coroutine: Coroutine[Any, Any, T]) -> T:
        try:
            loop = await asyncio.to_thread(self._ensure_loop)
        except BaseException:
            coroutine.close()
            raise
        result: concurrent.futures.Future[T] = concurrent.futures.Future()
        settled: concurrent.futures.Future[None] = concurrent.futures.Future()
        canceled = threading.Event()
        task_ref: list[asyncio.Task[T]] = []
        def start() -> None:
            if canceled.is_set():
                coroutine.close()
                result.cancel()
                settled.set_result(None)
                return
            task = loop.create_task(coroutine)
            task_ref.append(task)
            def done(completed: asyncio.Task[T]) -> None:
                if completed.cancelled():
                    result.cancel()
                elif (error := completed.exception()) is not None:
                    result.set_exception(error)
                else:
                    result.set_result(completed.result())
                settled.set_result(None)
            task.add_done_callback(done)
        with self._start_lock:
            if self._closed:
                coroutine.close()
                raise RuntimeError("Child supervisor closed")
            loop.call_soon_threadsafe(start)
        wrapped = asyncio.wrap_future(result)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            canceled.set()
            def cancel() -> None:
                if task_ref and not task_ref[0].cancelling():
                    task_ref[0].cancel()
            loop.call_soon_threadsafe(cancel)
            while not settled.done():
                try:
                    await asyncio.shield(asyncio.wrap_future(settled))
                except asyncio.CancelledError:
                    continue
            if wrapped.done() and not wrapped.cancelled():
                wrapped.exception()
            else:
                wrapped.cancel()
            raise

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
            return any(child.settling for child in self._children.values())

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
                          "label": child.spec.label, "timeout": child.spec.timeout,
                          "max_model_calls": child.spec.max_model_calls, "max_tokens": child.spec.max_tokens,
                          "inherit_context": child.spec.inherit_context, "model": child.spec.model,
                          "thinking": child.spec.thinking, "preset": child.spec.preset}}
            if child.effective:
                record["effective"] = dict(child.effective)
            if transcript:
                record['initial_state'] = child.initial_state
                if child.queue_uncertain:
                    record['queue_uncertain'] = True
                retained = set(image_parts(child.transcript))
                missing = (child.spec.images if child.initial_state in {'pending', 'uncertain'} else
                           tuple(part for part in child.spec.images if part not in retained))
                if missing:
                    record['task_images'] = images_to_dicts(missing)
                if child.undelivered:
                    from xdog.agent.supervision_queue import recovery_to_dicts
                    record['undelivered_inputs'] = recovery_to_dicts(child.undelivered)
                if child.returned_backups:
                    from xdog.agent.supervision_queue import recovery_to_dicts
                    record['returned_backups'] = recovery_to_dicts(child.returned_backups)
                if child.input_feedback:
                    record["input_feedback"] = list(child.input_feedback)
                record["messages"] = messages_to_dicts(list(child.transcript))
                if child.progress_sources_limited:
                    record["live_tools_limited"] = True
                if child.progress:
                    record["live_tools"] = [row.to_dict() for row in child.progress.values()]
                if child.partial is not None:
                    record["partial"] = messages_to_dicts([child.partial])[0]
                points = history_points(child.transcript)
                record["checkpoint"] = points[-1].index if points else -1
            return record

    async def start(self, task: str, *, access: str = "read-only", inherit_context: bool = True,
                    timeout: float = 120, config: dict[str, Any] | None = None,
                    images: tuple[ImageContent, ...] = ()) -> str:
        await asyncio.to_thread(validate_images, images)
        if (not isinstance(task, str) or not (task.strip() or images) or len(task.encode("utf-8")) > 16000
                or not isinstance(access, str) or access not in {"read-only", "inherit"}
                or type(inherit_context) is not bool or type(timeout) not in {float, int}
                or not math.isfinite(timeout) or not 1 <= timeout <= 600):
            raise ValueError("Invalid child task/access/budget")
        if config is not None:
            ChildConfig.from_dict(config)  # Validate shape before merging legacy explicit controls.
        # Explicit legacy controls participate only without a preset; otherwise
        # their default values must not overwrite a saved template's fields.
        base: dict[str, Any] = {"inherit_context": inherit_context, "timeout": timeout}
        if config is not None and config.get("preset"):
            base = {}
            if inherit_context is False:
                base["inherit_context"] = False
            if timeout != 120:
                base["timeout"] = timeout
        selected = self.presets.resolve({**base, **(config or {})})
        receipt = await asyncio.to_thread(input_receipt, task, images)
        return await self._bridge(self._start(task, access, selected, images, receipt))

    async def _start(self, task: str, access: str, config: ChildConfig, images: tuple[ImageContent, ...],
                     receipt: dict[str, Any]) -> str:
        with self._lock:
            if self._closed:
                raise RuntimeError("Child supervisor closed")
            if self._admission_pauses:
                raise RuntimeError("Session context/policy is changing; refresh before starting child work")
            if self.diagnostic:
                raise ValueError(self.diagnostic)
            if len(self._children) >= 16:
                raise ValueError("Close child records before starting more (16 record limit)")
            if sum(child.settling for child in self._children.values()) >= 3:
                raise ValueError("Child concurrency limit reached (3)")
            spec = ChildSpec(uuid.uuid4().hex[:12], task, access, config.timeout,
                             config.max_model_calls, config.max_tokens, config.inherit_context,
                             config.label, config.instructions, config.tools, config.model,
                             config.thinking, config.preset, images)
            self.capture(spec)
            child = _Child(spec, initial_receipt=receipt)
            self._children[spec.id] = child
            child.task = asyncio.create_task(self._run(child))
        self._notify(child)
        return spec.id

    def admission_receipt(self, identifier: str) -> dict[str, Any]:
        with self._lock:
            child = self._children.get(identifier)
            if child is None or not child.initial_receipt:
                raise ValueError("Child admission receipt unavailable; no reconstructed receipt")
            return {key: list(value) if isinstance(value, list) else value
                    for key, value in child.initial_receipt.items()}

    def _notify(self, child: _Child) -> None:
        with self._lock:
            child.updated = time.time()
        if not self._closed:
            try:
                self.publish({"type": "child_status", "child_id": child.spec.id, "snapshot": self.snapshot()})
            except Exception:
                # A disconnected UI must never strand a child or its persistence.
                pass

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
        if (len(transcript) > 2000 or
                _serialized_size(transcript) > MAX_TRANSCRIPT_BYTES):
            raise RuntimeError("Child aggregate transcript budget exhausted")
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

    async def _run(self, child: _Child) -> None:
        status, error = "failed", ""
        deadline: asyncio.Timeout | None = None
        budget_error = ""
        def unsubscribe() -> None:
            pass
        def publish(event: dict[str, Any]) -> None:
            if not self._closed:
                try:
                    self.publish({**event, "child_id": child.spec.id})
                except Exception:
                    pass
        try:
            deadline = asyncio.timeout(child.spec.timeout)
            await deadline.__aenter__()
            beginning = asyncio.create_task(asyncio.to_thread(self.begin, child.spec))
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
            child.agent = agent
            if self.persist_admission is not None:
                previous_checkpoint = agent._input_checkpoint
                async def checkpoint(event: AgentEvent) -> None:
                    if previous_checkpoint is not None:
                        await previous_checkpoint(event)
                    while child.queue_work:
                        await asyncio.sleep(0.005)
                    child.queue_work = True
                    from xdog.agent.supervision_transfer import settled_image_work
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
                        if len(history) > 2000 or _serialized_size(history) > MAX_TRANSCRIPT_BYTES:
                            raise RuntimeError('Child aggregate transcript budget exhausted')
                        canonical = image_parts(history)
                        missing = tuple(part for part in child.spec.images if part not in set(canonical))
                        check_collection((*canonical, *missing))
                        record['messages'] = messages_to_dicts(list(history))
                        points = history_points(history)
                        record['checkpoint'] = points[-1].index if points else -1
                        persist_admission = self.persist_admission
                        assert persist_admission is not None
                        await settled_image_work(lambda: persist_admission(record))
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
                    from xdog.agent.supervision_transfer import settled_image_work
                    try:
                        # Capture after any prior journal writer: it may have
                        # admitted another input while this checkpoint waited.
                        pending = tuple(replace(row, selected=False) for row in agent.pending_inputs())
                        persist = self.persist_inputs
                        assert persist is not None
                        await settled_image_work(lambda: persist(child.spec.id, pending))
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
                agent.set_tools(tuple(tool for tool in agent.state.tools if tool.name in allowed))
            validate_checkpoint(agent.messages, len(agent.messages) - 1)
            if any(tool.name in RESERVED_TOOLS for tool in child.agent.state.tools):
                raise ValueError("Child tools cannot supervise children or mutate a parent plan")
            def observe(event: AgentEvent) -> None:
                nonlocal budget_error
                try:
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
            unsubscribe = agent.subscribe(observe)
            original = child.agent.stream_fn
            def bounded(model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
                self._capture(child)
                if budget_error:
                    raise RuntimeError(budget_error)
                with self._lock:
                    remaining = child.spec.max_tokens - child.tokens
                    if child.calls >= child.spec.max_model_calls or remaining <= 0:
                        raise RuntimeError("Child model-call/token budget exhausted")
                    child.calls += 1
                limit = min(options.max_tokens, remaining) if options.max_tokens is not None else remaining
                return original(model, context, replace(options, max_tokens=limit))
            child.agent.set_stream_fn(bounded)
            self._capture(child)
            with self._lock:
                child.status = "running"
            self._notify(child)
            stream = await child.agent.prompt(child.spec.task, images=list(child.spec.images))
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
            if deadline is not None and deadline.expired():
                status, error = "timed-out", "Child absolute deadline expired"
            else:
                status = "canceled"
        except TimeoutError:
            status, error = "timed-out", "Child absolute deadline expired"
        except Exception as exc:
            status, error = "failed", str(exc)[:4000]
        finally:
            if deadline is not None:
                await deadline.__aexit__(None, None, None)
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
        if child.agent is not None:
            child.undelivered = tuple(replace(row, selected=False) for row in child.agent.drain_inputs())
        if self.finalize is not None:
            try:
                await self.finalize(child.spec)
            except Exception as exc:
                status, error = "failed", "Child resource cleanup failed: " + type(exc).__name__
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
                                                         "cancel", "wait", "close", "queue"}
                or not isinstance(identifier, str) or not identifier):
            raise ValueError("Invalid child control")
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
            from xdog.agent.supervision_queue import snapshot
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
        return QueuedInput('initial-task:' + child.spec.id, child.spec.task, 'follow_up', images=child.spec.images)

    @classmethod
    def _stopped_queue(cls, child: _Child) -> dict[str, Any]:
        from xdog.agent.supervision_queue import snapshot
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
            raise ValueError("Child is unavailable or belongs to another supervisor")
        # Live cancellation/close must remain reachable: _settle awaits its writer.
        # A stopped recovery writer instead owns the durable record itself; close
        # must not archive/remove that record while its reviewed update commits.
        if child.queue_work and (action in {'queue', 'steer', 'follow-up'}
                                 or action == 'close' and child.agent is None):
            raise RuntimeError('Child recovery storage still owns work; wait before changing it')
        if action == "queue":
            from xdog.agent.supervision_queue import change, recovery_to_dicts, request
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
                    from xdog.agent.supervision_queue import snapshot
                    return {'queue': snapshot(child.agent, identifier, child.status, child.undelivered)}
                agent = child.agent
                rows = child.live_returns.planned_commit(agent, identifier, child.status, command, return_owner)
                if self.persist_inputs is not None and rows is not None:
                    backup = None
                    if self.persist_return is not None:
                        assert agent is not None
                        original = next(row for row in agent.pending_inputs() if row.id == command['id'])
                        backup = replace(original, id='returned:' + command['token'], selected=False)
                        from xdog.agent.supervision_queue import recovery_from_dicts, recovery_to_dicts
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
                            from xdog.agent.supervision_transfer import prepare_recovery
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
                    from xdog.agent.supervision_transfer import prepare_recovery
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
                    from xdog.agent.supervision_queue import planned_change
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
                raise ValueError('Child is not running or its bounded input queue is full')
            await self._queue_admission(child, child.agent,
                QueuedInput(uuid.uuid4().hex, text, 'steer' if action == 'steer' else 'follow_up', images=images),
                lambda: True)
        elif action in {"cancel", "close"}:
            if child.settling and child.task is not None:
                with self._lock:
                    if child.status in {"queued", "running"}:
                        child.status = "canceling"
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
        children = tuple(self._children.values())
        for child in children:
            if child.agent is not None:
                child.agent.abort()
            if child.task is not None and not child.task.done() and not child.task.cancelling():
                child.task.cancel()
        for child in children:
            if child.task is not None:
                await self._join(child.task)
            while child.queue_work:
                await asyncio.sleep(0.005)  # Already admitted recovery writes settle before owner-loop shutdown.

    async def aclose(self) -> None:
        # Serialize creation/closure. Mark closed BEFORE draining, rejecting new starts.
        with self._start_lock:
            loop = self._loop
            first = not self._closed
            self._closed = True
            self._tool_return_owner = uuid.uuid4().hex
            future = (asyncio.run_coroutine_threadsafe(self._close_children(), loop)
                      if first and loop is not None else None)
        canceled = False
        if future is not None:
            wrapped = asyncio.wrap_future(future)
            while not wrapped.done():
                try:
                    await asyncio.shield(wrapped)
                except asyncio.CancelledError:
                    canceled = True
            wrapped.result()
            assert loop is not None
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            joining = asyncio.create_task(asyncio.to_thread(self._thread.join))
            while not joining.done():
                try:
                    await asyncio.shield(joining)
                except asyncio.CancelledError:
                    canceled = True
            joining.result()
        if canceled:
            raise asyncio.CancelledError

    def abort(self) -> None:
        """Cancel current children without closing the session's reusable supervisor."""
        with self._start_lock:
            if self._closed:
                return
            self._tool_return_owner = uuid.uuid4().hex
            if self._loop is None:
                return
            future = asyncio.run_coroutine_threadsafe(self._close_children(), self._loop)
        def consume(result: concurrent.futures.Future[None]) -> None:
            if not result.cancelled():
                result.exception()
        future.add_done_callback(consume)

    def close(self) -> None:
        """Request background disposal; use aclose to await lifecycle settlement."""
        with self._start_lock:
            if self._closed:
                return
            self._closed = True
            self._tool_return_owner = uuid.uuid4().hex
            loop = self._loop
            if loop is None:
                return
            future = asyncio.run_coroutine_threadsafe(self._close_children(), loop)
        def done(value: concurrent.futures.Future[None]) -> None:
            try:
                value.result()
            finally:
                loop.call_soon_threadsafe(loop.stop)
        future.add_done_callback(done)

    def tools(self) -> tuple[AgentTool, ...]:
        async def spawn(call_id: str, args: dict[str, Any], *extra: Any, **kwargs: Any) -> AgentToolResult:
            identifier = await self.start(args["task"], access=args.get("access", "read-only"),
                                          inherit_context=args.get("inherit_context", True),
                                          timeout=args.get("timeout", 120), config=args.get("config"))
            return AgentToolResult(content=(TextContent(text=identifier),))
        def validate_control(args: dict[str, Any]) -> None:
            # The generic lightweight schema checker tolerates extra keys. Enforce
            # this authority-sensitive tool's exact shape before the host hook too.
            if (set(args) - {"action", "id", "text"} or not isinstance(args.get("action"), str)
                    or args["action"] not in {"list", "presets", "inspect", "steer", "follow-up",
                                              "cancel", "wait", "close", "queue"}
                    or "text" in args and not isinstance(args["text"], str)
                    or "id" in args and (not isinstance(args["id"], str) or not args["id"])
                    or args["action"] not in {"list", "presets"} and (
                        not isinstance(args.get("id"), str) or not args["id"])):
                raise ValueError("Invalid child control tool arguments; no caller-supplied owner")
        async def action(call_id: str, args: dict[str, Any], cancel: asyncio.Event | None = None,
                         *extra: Any, **kwargs: Any) -> AgentToolResult:
            validate_control(args)
            with self._start_lock:
                owner = self._tool_return_owner
            def alive() -> bool:
                with self._start_lock:
                    return (not self._closed and self._tool_return_owner == owner
                            and (cancel is None or not cancel.is_set()))
            if not alive():
                raise ValueError("Main-Agent child control owner canceled/closed")
            if args["action"] == "list":
                result = self.snapshot()
            elif args["action"] == "presets":
                result = {"presets": self.presets.snapshot()}
            else:
                result = await self.control(args["action"], args["id"], args.get("text", ""),
                                            return_owner=owner, return_alive=alive)
            # Prepared bytes already passed settled validation on the supervisor loop.
            # Keep binary content typed, not base64 disguised as model-visible prose.
            images: tuple[ImageContent, ...] = ()
            if isinstance(result, dict) and "image_recovery" in result:
                payload = result["image_recovery"]
                images = tuple(ImageContent(data=row["data"], mime_type=row["mime_type"])
                               for row in payload["images"])
                result = {**result, "image_recovery": {"receipt": payload["receipt"], "image_count": len(images)}}
            return AgentToolResult(content=(TextContent(text=json.dumps(result)), *images))
        return (
            AgentTool(name="agent_spawn", description="Start a bounded session-owned child; default read-only.",
                      parameters={"type": "object", "required": ["task"], "additionalProperties": False,
                                  "properties": {"task": {"type": "string"},
                                                 "access": {"enum": ["read-only", "inherit"]},
                                                 "inherit_context": {"type": "boolean"},
                                                 "timeout": {"type": "number", "minimum": 1, "maximum": 600},
                                                 "config": {"type": "object", "additionalProperties": False,
                                                     "properties": {
                                                         "preset": {"type": ["string", "null"], "maxLength": 64},
                                                         "model": {"type": ["string", "null"], "maxLength": 256},
                                                         "thinking": {"enum": [None, "off", "minimal", "low", "medium",
                                                                                "high", "xhigh"]},
                                                         "label": {"type": "string", "maxLength": 120},
                                                         "instructions": {"type": "string", "maxLength": 16000},
                                                         "tools": {"type": ["array", "null"], "maxItems": 128,
                                                                   "items": {"type": "string"}, "uniqueItems": True},
                                                         "inherit_context": {"type": "boolean"},
                                                         "timeout": {"type": "number", "minimum": 1, "maximum": 600},
                                                         "max_model_calls": {"type": "integer", "minimum": 1,
                                                                             "maximum": 16},
                                                         "max_tokens": {"type": "integer", "minimum": 1,
                                                                        "maximum": 32000}}}}},
                      execute=spawn),
            AgentTool(name="agent_control", description=(
                                   "List/inspect/steer/cancel/wait/close or control this session's child queues. "
                                   "Queue controls use text JSON: list, edit/remove/move/return, stopped recovery, "
                                   "or return_prepare/return_commit/return_cancel for live queued images. "
                                   "Prepare requires id/revision and returns typed images plus return_token and "
                                   "image_receipt; commit requires that exact id/revision/token/receipt. "
                                   "Prepare does not remove input; cancellation, changed selection/owner or expiry "
                                   "refuses commit. Never automatically replay an uncertain mutation. "
                                   "Ownership is supplied by the supervisor, never by tool arguments."),
                      parameters={"type": "object", "required": ["action"], "additionalProperties": False,
                                  "properties": {
                                      "action": {"enum": ["list", "presets", "inspect", "steer", "follow-up",
                                                          "cancel", "wait", "close", "queue"]},
                                      "id": {"type": "string"}, "text": {"type": "string"}}},
                      validate_arguments=validate_control, execute=action),
        )
