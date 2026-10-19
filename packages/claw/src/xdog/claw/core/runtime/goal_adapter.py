"""Optional group-owned Goal ports; no planner, observer policy or second loop."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from collections.abc import Callable, Coroutine, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from xdog.agent.async_work import run_owned, wait_owned
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.agent.session_input import SessionInput
from xdog.agent.supervision import ChildSpec, settle_on
from xdog.agent.supervision_store import ChildStore
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.runtime.input_adapter import freeze_input
from xdog.claw.core.types import ExternalInput
from xdog.goal.host import ChildStatus, EnqueueReceipt, OperationContext, ReviewerSpec, WorkerSpec
from xdog.goal.models import Notice, Owner, WorkerRef
from xdog.goal.resources import role_instructions
from xdog.goal.service import GoalService
from xdog.goal.store import GoalStore
from xdog.goal.tools import create_context_tool, create_goal_tool, create_report_tool
from xdog.goal.ui_binding import GoalDisplay

if TYPE_CHECKING:
    from xdog.claw.core.inputs.consumer import InputService
    from xdog.claw.core.runtime.child_agents import ClawChildren
    from xdog.claw.core.runtime.group import GroupRuntime
    from xdog.claw.core.runtime.session import AgentSession

logger = logging.getLogger(__name__)
Scope = tuple[str, str]
Key = tuple[str, str, str]


@dataclass
class _Admission:
    host: ClawGoalHost
    role: WorkerSpec | ReviewerSpec
    claimed: bool = False


_admission: ContextVar[_Admission | None] = ContextVar("claw_goal_admission", default=None)


class ClawGoalHost:
    def __init__(self, runtime: GroupRuntime, inputs: InputService, namespace: str) -> None:
        self.runtime, self.inputs, self.namespace = runtime, inputs, namespace
        self.loop = asyncio.get_running_loop()
        self.scope = InputScope(runtime.group.id, runtime.workspace_dir)
        self.epoch = uuid.uuid4().hex
        self.service: GoalService
        self._lock = threading.Lock()
        self._roles: dict[WorkerRef, WorkerSpec | ReviewerSpec] = {}
        self._references: dict[Key, WorkerRef] = {}
        self._children: dict[Scope, ClawChildren] = {}
        self._subscriptions: dict[Scope, Callable[[], None]] = {}
        self._bound: dict[WorkerRef, tuple[AgentTool, ...]] = {}
        self._tool: AgentTool | None = None
        self._attached: AgentSession | None = None
        self._display: GoalDisplay | None = None
        self._retired_displays: set[GoalDisplay] = set()
        self._jobs: set[asyncio.Task[None]] = set()
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None

    @classmethod
    async def open(cls, runtime: GroupRuntime, inputs: InputService) -> ClawGoalHost:
        legacy = runtime.data_dir / "groups" / runtime.group.id / "goals.json"
        if await run_owned(os.path.lexists, legacy):
            raise ValueError("Legacy Goal data is unsupported; select a new data directory explicitly, no upgrade")
        scope = InputScope(runtime.group.id, runtime.workspace_dir)
        namespace = await inputs.store.namespace(scope)
        host = cls(runtime, inputs, namespace)
        store = await GoalStore.open(
            runtime.data_dir / "groups" / runtime.group.id / "goals" / "journal.sqlite3", host.owner
        )
        host.service = GoalService(store, host, on_change=host._changed)
        try:
            for goal in await host.service.list():
                pairs: list[tuple[WorkerRef, WorkerSpec | ReviewerSpec]] = []
                for task in goal.tasks:
                    for attempt in task.attempts:
                        if attempt.worker is not None:
                            pairs.append(
                                (
                                    attempt.worker,
                                    WorkerSpec(goal.id, task.id, attempt.id, attempt.assignment, attempt.access),
                                )
                            )
                if goal.review is not None and goal.review.worker is not None:
                    pairs.append(
                        (
                            goal.review.worker,
                            ReviewerSpec(
                                goal.id,
                                goal.review.id,
                                goal.review.criteria_revision,
                                "Inspect original saved independent review",
                            ),
                        )
                    )
                for reference, role in pairs:
                    session_id, branch = host._locator(reference)
                    key = session_id, branch, reference.id
                    if key in host._references and host._references[key] != reference:
                        raise ValueError("Saved Goal execution binding conflict")
                    host._references[key] = reference
                    host._roles[reference] = role
        except BaseException:
            await store.close()
            raise
        return host

    @property
    def owner(self) -> Owner:
        return Owner(f"claw:{self.namespace}:{self.runtime.group.id}", self.runtime.workspace_dir)

    def _check(self) -> None:
        if self._closing or self.owner != self.service.store.owner:
            raise ValueError("Claw Goal owner binding changed or closed")

    def _scope(self, children: ClawChildren) -> Scope:
        return children.session.meta.session_id, children.store.root.name.removeprefix("branch-")

    @staticmethod
    def _locator(reference: WorkerRef) -> Scope:
        try:
            value = json.loads(reference.execution)
        except (ValueError, TypeError) as exc:
            raise ValueError("Unsupported Goal execution reference") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"v", "n", "s", "b"}
            or type(value["v"]) is not int
            or value["v"] != 1
            or not isinstance(value["n"], str)
            or re.fullmatch("[a-f0-9]{32}", value["n"]) is None
            or any(
                not isinstance(value[key], str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value[key]) is None
                for key in ("s", "b")
            )
        ):
            raise ValueError("Invalid Goal execution reference locator")
        return value["s"], value["b"]

    def attach(self, session: AgentSession) -> None:
        self._check()
        if session._runtime is not self.runtime:
            raise ValueError("Goal host cannot attach to another group")
        if self._attached is session:
            return
        for scope, children in tuple(self._children.items()):
            if children is not session.children:
                if children.supervisor.active:
                    raise RuntimeError("Actual child work still owns the original group context")
                self._subscriptions.pop(scope)()
                self._children.pop(scope)
        scope = self._scope(session.children)
        session.children.goal_host = self
        self._children[scope] = session.children
        self._subscriptions[scope] = session.children.supervisor.subscribe(
            lambda event: self._event(session.children, event)
        )
        tool = create_goal_tool(self.service, lambda: OperationContext(session._user_operation_authorized))
        previous = self._tool
        selected = self.runtime._enabled_tools
        self.runtime.capability_tools = (tool,) if selected is None or "goal" in selected else ()
        base = tuple(t for t in session.agent.state.tools if t is not previous)
        if any(t.name == "goal" for t in base):
            raise ValueError("Goal tool name already owned; refusing capability replacement")
        session.agent.set_tools((*base, *self.runtime.capability_tools))
        self._tool, self._attached = tool, session
        if self._display is not None:
            previous_display = self._display
            previous_display.retire()
            self._retired_displays.add(previous_display)
            self._display = None

            async def close_display() -> None:
                await previous_display.aclose()
                self._retired_displays.discard(previous_display)

            self._schedule(close_display)
        extensions = session.extensions
        if "goal_ui" in extensions.enabled:
            self._display = GoalDisplay(self.service, extensions.views,
                valid=lambda: not self._closing and self._attached is session
                    and self.runtime._active_session is session and session.extensions is extensions
                    and self.owner == self.service.store.owner,
                notify=extensions.notify_view_changed)
        self._changed()  # Recover original persisted notices once; never poll active Goals.

    def capture(self, children: ClawChildren, spec: ChildSpec) -> None:
        ticket = _admission.get()
        if ticket is None or ticket.host is not self:
            return
        with self._lock:
            if ticket.claimed:
                return
            self._check()
            session_id, branch = self._scope(children)
            locator = json.dumps({"v": 1, "n": self.epoch, "s": session_id, "b": branch}, separators=(",", ":"))
            reference = WorkerRef(spec.id, locator)  # Validate bound before actual execution admission.
            ticket.claimed = True
            self._references[(session_id, branch, spec.id)] = reference
            self._roles[reference] = ticket.role

    def _reference(self, children: ClawChildren, identifier: str) -> WorkerRef | None:
        with self._lock:
            return self._references.get((*self._scope(children), identifier))

    def role(self, children: ClawChildren, identifier: str) -> WorkerSpec | ReviewerSpec | None:
        reference = self._reference(children, identifier)
        with self._lock:
            return self._roles.get(reference) if reference is not None else None

    async def _start(self, spec: WorkerSpec | ReviewerSpec) -> WorkerRef:
        self._check()
        session = await self.runtime.prepare_session(allow_reset=False)
        self.attach(session)
        role = "worker" if isinstance(spec, WorkerSpec) else "reviewer"
        ticket = _Admission(self, spec)
        token = _admission.set(ticket)
        try:
            identifier = await session.children.supervisor.start(
                spec.prompt,
                access=spec.access,
                inherit_context=False,
                config={"label": "goal-" + role, "instructions": await role_instructions(role)},
            )
            reference = self._reference(session.children, identifier)
            if not ticket.claimed or reference is None:
                raise ValueError("Goal role did not receive actual execution admission")
            return reference
        finally:
            _admission.reset(token)

    async def start_worker(self, spec: WorkerSpec) -> WorkerRef:
        return await self._start(spec)

    async def start_reviewer(self, spec: ReviewerSpec) -> WorkerRef:
        return await self._start(spec)

    async def role_tools(self, children: ClawChildren, spec: ChildSpec) -> tuple[AgentTool, ...]:
        role = self.role(children, spec.id)
        reference = self._reference(children, spec.id)
        if role is None or reference is None:
            return ()

        async def originals() -> tuple[AgentTool, ...]:
            self._check()
            if isinstance(role, WorkerSpec):
                await self.service.bind_worker(role.goal_id, role.task_id, role.attempt_id, reference)
                return (
                    create_context_tool(self.service, role.goal_id, task_id=role.task_id, attempt_id=role.attempt_id),
                    create_report_tool(self.service, role.goal_id, role.task_id, role.attempt_id),
                )
            await self.service.bind_reviewer(role.goal_id, role.review_id, reference)
            return (create_context_tool(self.service, role.goal_id, review_id=role.review_id),)

        tools = []
        for original in await settle_on(self.loop, originals()):

            def bridge(tool: AgentTool) -> AgentTool:
                async def execute(
                    call: str,
                    params: dict[str, Any],
                    cancel: asyncio.Event | None = None,
                    on_update: Any = None,
                    ctx: Any = None,
                ) -> AgentToolResult:
                    async def invoke() -> AgentToolResult:
                        assert tool.execute is not None
                        return await tool.execute(call, params, None, None, ctx)

                    return await settle_on(self.loop, invoke())

                return replace(tool, execute=execute)

            tools.append(bridge(original))
        result = tuple(tools)
        with self._lock:
            self._bound[reference] = result
        return result

    def role_call(self, children: ClawChildren, identifier: str, name: str, tools: Sequence[AgentTool]) -> bool:
        reference = self._reference(children, identifier)
        with self._lock:
            bound = self._bound.get(reference, ()) if reference is not None else ()
        actual = next((tool for tool in tools if tool.name == name), None)
        return actual is not None and any(actual is tool for tool in bound)

    async def child_status(self, worker: WorkerRef) -> ChildStatus:
        self._check()
        with self._lock:
            known = worker in self._roles
        if not known:
            return ChildStatus(worker, "unknown")
        scope = self._locator(worker)
        children = self._children.get(scope)
        row: dict[str, Any] | None
        if children is not None and not children.supervisor._closed:
            try:
                row = await children.supervisor.control("inspect", worker.id)
            except ValueError:
                return ChildStatus(worker, "unknown")
        else:

            def original() -> dict[str, Any] | None:
                sessions = self.runtime.transcript_store.list_sessions(self.scope.group_id)
                if not any(record["session_id"] == scope[0] for record in sessions):
                    raise ValueError("Goal execution belongs to another group or missing original session")
                store = ChildStore.scoped(self.runtime.transcript_store._sessions_dir, *scope)
                return next((row for row in store.load() if row["id"] == worker.id), None)

            row = await run_owned(original)
            if row is None:
                return ChildStatus(worker, "unknown")
        status = (
            "settling"
            if row["settling"]
            else {"completed": "succeeded", "failed": "failed", "canceled": "cancelled", "interrupted": "unknown"}.get(
                row["status"], "unknown"
            )
        )
        return ChildStatus(worker, status, row.get("result", ""), row.get("error", ""))

    async def cancel_child(self, worker: WorkerRef) -> None:
        self._check()
        with self._lock:
            if worker not in self._roles:
                raise ValueError("Goal child execution reference is not owned by this group")
        children = self._children.get(self._locator(worker))
        if children is not None and not children.supervisor._closed:
            await children.supervisor.control("cancel", worker.id)
        elif (await self.child_status(worker)).state in {"unknown", "running", "settling"}:
            raise ValueError("Original Goal execution is unknown; no offline replay or replacement cancellation")

    def _envelope(self, notice: Notice) -> InputEnvelope:
        message = SessionInput(
            notice.id,
            "automatic",
            "goal",
            notice.instruction,
            (),
            {"goal_id": notice.goal_id, "owner": self.service.store.owner.key},
        )
        value = ExternalInput(
            group_id=self.scope.group_id,
            content="",
            sender="goal",
            channel="",
            canonical=message,
            metadata={"input_origin": "external"},
        )
        return freeze_input(value, self.scope.workspace, message_id=notice.id)

    async def _namespace(self) -> None:
        self._check()
        if await self.inputs.store.namespace(self.scope) != self.namespace:
            raise ValueError("Goal notice inbox namespace changed; no automatic transfer")

    async def enqueue(self, notice: Notice) -> EnqueueReceipt:
        await self._namespace()
        _, duplicate = await self.inputs.submit(
            self._envelope(notice), max_pending=self.runtime.group.config.max_queued_messages
        )
        return EnqueueReceipt(self.owner.key, notice.id, "duplicate" if duplicate else "accepted")

    async def receipt(self, notice: Notice) -> EnqueueReceipt | None:
        await self._namespace()
        record = await self.inputs.store.get(self.scope, notice.id)
        if record is None:
            return None
        if record.envelope != self._envelope(notice):
            raise ValueError("Goal notice receipt owner/content conflict")
        return EnqueueReceipt(self.owner.key, notice.id, "accepted")

    def _schedule(self, operation: Callable[[], Coroutine[Any, Any, None]]) -> None:
        if self._closing or self.loop.is_closed():
            return

        def admit() -> None:
            if self._closing:
                return
            task = self.loop.create_task(operation())
            self._jobs.add(task)

            def done(result: asyncio.Task[None]) -> None:
                self._jobs.discard(result)
                if not result.cancelled() and result.exception() is not None:
                    logger.warning("Goal obligation remains persisted after adapter failure: %s", result.exception())

            task.add_done_callback(done)

        self.loop.call_soon_threadsafe(admit)

    def _changed(self) -> None:
        self._schedule(self.service.drain_notifications)
        if self._display is not None:
            self._display.changed()

    def _event(self, children: ClawChildren, event: dict[str, Any]) -> None:
        role = self.role(children, event.get("child_id", ""))
        if role is None:
            return
        captured = children.supervisor.capture_source(event["child_id"])
        if not captured.settling:

            async def settle() -> None:
                await self.service.reconcile(role.goal_id)
                await self.service.drain_notifications()

            self._schedule(settle)

    async def flush(self) -> None:
        await asyncio.sleep(0)
        while self._jobs:
            await asyncio.gather(*tuple(self._jobs))
            await asyncio.sleep(0)
        await self.service.drain_notifications()

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closing = True
            for remove in self._subscriptions.values():
                remove()
            self._subscriptions.clear()
            displays = (*self._retired_displays, *((self._display,) if self._display is not None else ()))
            for display in displays:
                display.retire()

            async def close() -> None:
                await asyncio.gather(*(display.aclose() for display in displays))
                self._retired_displays.clear()
                self._display = None
                await asyncio.gather(*tuple(self._jobs), return_exceptions=True)
                await self.service.close()
                self.runtime.capability_tools = tuple(
                    tool for tool in self.runtime.capability_tools if tool is not self._tool
                )
                self._children.clear()
                self._attached = None

            self._close_task = asyncio.create_task(close())
        await wait_owned(self._close_task)
