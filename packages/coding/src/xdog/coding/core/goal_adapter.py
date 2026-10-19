"""Explicit optional Goal host over existing Coding execution and durable intake.

Imported only by trusted opt-in startup. No new Agent/controller, CLI callback or
business state in Coding: Goal owns its journal, roles and notification outbox.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import uuid
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from xdog.agent.async_work import run_owned, wait_owned
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.agent.supervision import ChildSpec, settle_on
from xdog.coding.core.sessions.inbox_models import InboxMessage, message_data
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.goal.host import ChildStatus, EnqueueReceipt, OperationContext, ReviewerSpec, WorkerSpec
from xdog.goal.models import Notice, Owner, WorkerRef
from xdog.goal.resources import role_instructions
from xdog.goal.service import GoalService
from xdog.goal.store import GoalStore
from xdog.goal.tools import create_context_tool, create_goal_tool, create_report_tool
from xdog.goal.ui_binding import GoalDisplay

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession

logger = logging.getLogger(__name__)


@dataclass
class _Admission:
    host: CodingGoalHost
    spec: WorkerSpec | ReviewerSpec
    claimed: bool = False


_admission: ContextVar[_Admission | None] = ContextVar("coding_goal_admission", default=None)


class CodingGoalHost:
    def __init__(
        self, session: AgentSession, namespace: str, inbox: InboxStore, context: Callable[[], OperationContext]
    ) -> None:
        self.session = session
        self.children = session.children
        self.loop = asyncio.get_running_loop()
        self.session_id = session.session_id
        self.branch = session.session_data.active_branch
        self.workspace = session.working_dir
        self.namespace = namespace
        self.inbox = inbox
        self._context = context
        self.execution = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._roles: dict[str, WorkerSpec | ReviewerSpec] = {}
        self._references: dict[str, WorkerRef] = {}
        self.bound_tools: dict[str, tuple[AgentTool, ...]] = {}
        self._jobs: set[asyncio.Task[None]] = set()
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None
        self._unsubscribe: Callable[[], None] = lambda: None
        self._display: GoalDisplay | None = None
        self.service: GoalService
        self.tool: AgentTool

    @classmethod
    async def open(
        cls, session: AgentSession, *, context: Callable[[], OperationContext] | None = None
    ) -> CodingGoalHost:
        if session.children.goal_host is not None:
            raise ValueError("This Coding child owner already has a Goal binding")
        if any(tool.name == "goal" for tool in session.agent.state.tools):
            raise ValueError("Goal tool name is already registered; refusing capability replacement")
        inbox = InboxStore(session.session_manager._dir, session.session_manager._dir.parent / "inbox")
        namespace = await run_owned(inbox.scope_id, session.session_id, session.working_dir)
        host = cls(session, namespace, inbox, context or (lambda: OperationContext(False)))
        scope = hashlib.sha256(host.owner.key.encode()).hexdigest()
        store = await GoalStore.open(
            session.session_manager._dir.parent / "goals" / scope / "journal.sqlite3", host.owner
        )
        host.service = GoalService(store, host, on_change=host._changed)
        host.tool = create_goal_tool(host.service, host._context)
        try:
            await host._restore_references()
            session.children.goal_host = host
            host._unsubscribe = host.children.supervisor.subscribe(host._event)
            session.agent.set_tools((*session.agent.state.tools, host.tool))
            extensions = session.extensions
            if "goal_ui" in extensions.enabled:
                host._display = GoalDisplay(host.service, extensions.views,
                    valid=lambda: not host._closing and host.owner == store.owner
                        and session.extensions is extensions,
                    notify=extensions.notify_view_changed)
                host._display.changed()
        except BaseException:
            host._unsubscribe()
            session.children.goal_host = None
            await store.close()
            raise
        return host

    @property
    def owner(self) -> Owner:
        # Scope is a host-produced binding, never a source label or model input.
        current = self.session
        branch = current.session_data.active_branch
        key = f"coding:{self.namespace}:{current.session_id}:{branch}"
        if current.children is not self.children:
            key += ":retired-child-owner"
        return Owner(key, current.working_dir)

    def _check(self) -> None:
        if self._closing or self.owner != self.service.store.owner:
            raise ValueError("Coding Goal owner binding changed or closed")

    async def _restore_references(self) -> None:
        # The Goal journal binds original executions to this exact owner. The
        # actual ChildStore catalog still decides success/settlement; missing
        # children remain unknown. Loading references never starts an execution.
        for goal in await self.service.list():
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
                            goal.id, goal.review.id, goal.review.criteria_revision, "Inspect original saved review"
                        ),
                    )
                )
            for reference, role in pairs:
                previous = self._references.get(reference.id)
                if previous is not None and (previous != reference or self._roles[reference.id] != role):
                    raise ValueError("Saved Goal execution binding conflict")
                self._references[reference.id] = reference
                self._roles[reference.id] = role

    def _ref(self, identifier: str) -> WorkerRef:
        return self._references.get(identifier, WorkerRef(identifier, self.execution + ":" + identifier))

    def capture(self, spec: ChildSpec) -> None:
        ticket = _admission.get()
        if ticket is None or ticket.host is not self:
            return
        with self._lock:
            if ticket.claimed:
                return  # A copied ContextVar is not permission for a second child.
            self._check()
            ticket.claimed = True
            self._roles[spec.id] = ticket.spec
            self._references[spec.id] = WorkerRef(spec.id, self.execution + ":" + spec.id)

    def role(self, identifier: str) -> WorkerSpec | ReviewerSpec | None:
        with self._lock:
            return self._roles.get(identifier)

    async def _start(self, spec: WorkerSpec | ReviewerSpec) -> WorkerRef:
        self._check()
        role = "worker" if isinstance(spec, WorkerSpec) else "reviewer"
        ticket = _Admission(self, spec)
        token = _admission.set(ticket)
        try:
            identifier = await self.children.supervisor.start(
                spec.prompt,
                access=spec.access,
                inherit_context=False,
                config={"label": "goal-" + role, "instructions": await role_instructions(role)},
            )
            if not ticket.claimed:
                raise ValueError("Goal role did not receive actual child admission")
            return self._ref(identifier)
        finally:
            _admission.reset(token)

    async def start_worker(self, spec: WorkerSpec) -> WorkerRef:
        return await self._start(spec)

    async def start_reviewer(self, spec: ReviewerSpec) -> WorkerRef:
        return await self._start(spec)

    async def role_tools(self, spec: ChildSpec) -> tuple[AgentTool, ...]:
        role = self.role(spec.id)
        if role is None:
            return ()

        async def originals() -> tuple[AgentTool, ...]:
            self._check()
            if isinstance(role, WorkerSpec):
                await self.service.bind_worker(role.goal_id, role.task_id, role.attempt_id, self._ref(spec.id))
                return (
                    create_context_tool(self.service, role.goal_id, task_id=role.task_id, attempt_id=role.attempt_id),
                    create_report_tool(self.service, role.goal_id, role.task_id, role.attempt_id),
                )
            await self.service.bind_reviewer(role.goal_id, role.review_id, self._ref(spec.id))
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
                    assert tool.execute is not None

                    # Bound tools own host data only and do not publish progress;
                    # never pass a child-loop cancellation Event into that loop.
                    async def invoke() -> AgentToolResult:
                        assert tool.execute is not None
                        return await tool.execute(call, params, None, None, ctx)

                    return await settle_on(self.loop, invoke())

                return replace(tool, execute=execute)

            tools.append(bridge(original))
        result = tuple(tools)
        with self._lock:
            self.bound_tools[spec.id] = result
        return result

    def role_call(self, identifier: str, name: str, actual_tools: Sequence[AgentTool]) -> bool:
        with self._lock:
            bound = self.bound_tools.get(identifier, ())
        selected = next((tool for tool in actual_tools if tool.name == name), None)
        return selected is not None and any(selected is tool for tool in bound)

    async def child_status(self, worker: WorkerRef) -> ChildStatus:
        self._check()
        if self._ref(worker.id) != worker or self.role(worker.id) is None:
            return ChildStatus(worker, "unknown")
        try:
            row = await self.children.supervisor.control("inspect", worker.id)
        except ValueError:
            return ChildStatus(worker, "unknown")
        status = (
            "settling"
            if row["settling"]
            else {"completed": "succeeded", "failed": "failed", "canceled": "cancelled", "interrupted": "unknown"}.get(
                row["status"], "running"
            )
        )
        return ChildStatus(worker, status, row.get("result", ""), row.get("error", ""))

    async def cancel_child(self, worker: WorkerRef) -> None:
        self._check()
        if self._ref(worker.id) != worker or self.role(worker.id) is None:
            raise ValueError("Goal child execution reference is not owned by this Coding binding")
        await self.children.supervisor.control("cancel", worker.id)

    def _message(self, notice: Notice) -> InboxMessage:
        return InboxMessage(
            self.session_id,
            self.workspace,
            notice.id,
            notice.instruction,
            (),
            "goal",
            {"goal_id": notice.goal_id, "owner": self.service.store.owner.key},
        )

    async def enqueue(self, notice: Notice) -> EnqueueReceipt:
        self._check()
        value = await run_owned(self.inbox.enqueue, self._message(notice), expected_branch_id=self.branch)
        self._check()
        return EnqueueReceipt(self.owner.key, notice.id, "duplicate" if value.duplicate else "accepted")

    async def receipt(self, notice: Notice) -> EnqueueReceipt | None:
        self._check()
        rows = await run_owned(self.inbox.records, self.session_id, self.workspace)
        self._check()
        found = next((row for row in rows if row.message.message_id == notice.id), None)
        if found is None:
            return None
        if found.branch_id != self.branch or message_data(found.message) != message_data(self._message(notice)):
            raise ValueError("Goal notice receipt owner/content conflict")
        return EnqueueReceipt(self.owner.key, notice.id, "accepted")

    def _schedule(self, operation: Callable[[], Any]) -> None:
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

    def _event(self, event: dict[str, Any]) -> None:
        identifier = event.get("child_id", "")
        role = self.role(identifier)
        if role is None:
            return
        capture = self.children.supervisor.capture_source(identifier)
        if not capture.settling:

            async def settle() -> None:
                await self.service.reconcile(role.goal_id)
                await self.service.drain_notifications()

            self._schedule(settle)

    async def flush(self) -> None:
        """Join admitted data obligations, then hand off notices; never starts a model."""
        await asyncio.sleep(0)
        while self._jobs:
            await asyncio.gather(*tuple(self._jobs))
            await asyncio.sleep(0)
        await self.service.drain_notifications()

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._unsubscribe()
            if self._display is not None:
                self._display.retire()

            async def close() -> None:
                if self._display is not None:
                    await self._display.aclose()
                await asyncio.gather(*tuple(self._jobs), return_exceptions=True)
                await self.service.close()
                if self.session.children is self.children:
                    self.children.goal_host = None
                    if not self.session.agent.state.is_streaming:
                        self.session.agent.set_tools(
                            tuple(t for t in self.session.agent.state.tools if t is not self.tool)
                        )

            self._close_task = asyncio.create_task(close())
        await wait_owned(self._close_task)
