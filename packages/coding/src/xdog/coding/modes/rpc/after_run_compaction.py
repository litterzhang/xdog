"""Independently negotiated RPC compaction after the original prompt task settles."""
from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Callable
from typing import Any

from xdog.agent.extensions.compaction_request import AfterRunCompactions
from xdog.agent.extensions.invocation import current_invocation

_OUTCOME: contextvars.ContextVar[Callable[[bool], None] | None] = contextvars.ContextVar(
    'rpc_after_run_compaction_outcome', default=None)


def producer_outcome(success: bool) -> None:
    callback = _OUTCOME.get()
    if callback is not None:
        callback(success)


class RpcAfterRunCompaction:
    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.enabled = False
        self.generation = 0
        self.producer: asyncio.Task[Any] | None = None
        self.success = False
        self.execution: tuple[str, str] | None = None
        self.queue = AfterRunCompactions(lambda event: owner.loop.call_soon_threadsafe(owner.emit, event))
        self.jobs: set[asyncio.Task[None]] = set()

    def negotiate(self, enabled: bool) -> None:
        self.generation += 1
        self.enabled = enabled
        self.queue.discard('capability-withdrawn')
        owner = self.owner
        broker = getattr(owner.session, 'extension_dialogs', None)
        if broker is not None:
            broker.cancel_where(lambda dialog: (
                dialog.get('operation') == 'compact' and dialog.get('after_run') is True))
        if (self.execution is not None and owner.navigation is not None and owner.navigation.owner == self.execution
                and owner.navigation.compacting):
            owner.navigation.cancel({'request_id': self.execution[0], 'expected_session': self.execution[1]})
        if owner.scope is not None:
            owner.session.extensions.compaction_request_handler = self.ask if enabled else None

    def bind(self) -> contextvars.Token[Any]:
        owner = self.owner
        self.queue.discard('new-producer')
        task = asyncio.current_task() if owner.control is None else None
        self.producer, self.success = task, False
        if task is not None:
            task.add_done_callback(self._done)
        return _OUTCOME.set(lambda value: setattr(self, 'success', value) if self.producer is task else None)

    def _done(self, task: asyncio.Task[Any]) -> None:
        if self.owner.closed or not self.enabled:
            return
        async def drain() -> None:
            if self.producer is not task:
                return
            agent = self.owner.session.agent
            producer = agent._producer_task
            if producer is not None and not producer.done():
                await asyncio.shield(asyncio.gather(producer, return_exceptions=True))
            item = self.queue.take()
            owner = self.owner
            if item is None or owner.navigation is None or owner.navigation.busy or owner.scope is not None:
                return
            packet = {'action': 'compact', 'request_id': item.id, 'expected_session': item.session_id,
                      'session_revision': owner.navigation.revision, 'instructions': item.instructions}
            try:
                owner.navigation.start(packet, turn_running=False, extension_running=False, compact_alive=item.alive)
                self.execution = item.id, item.session_id
            except (ValueError, RuntimeError, OSError):
                owner.emit({'type': 'extension_compaction_after_run', 'request_id': item.id,
                            'session_id': item.session_id, 'status': 'discarded'})
        job = asyncio.create_task(drain())
        self.jobs.add(job)
        def done(work: asyncio.Task[None]) -> None:
            self.jobs.discard(work)
            if not work.cancelled():
                work.exception()
        job.add_done_callback(done)

    async def ask(self, extension: str, dialog: dict[str, Any]) -> bool:
        owner, task, generation = self.owner, self.producer, self.generation
        scope, session = owner.scope, owner.session
        lease = current_invocation()
        if (not self.enabled or scope is None or task is None or task.done() or lease is None or lease.child_id
                or lease.session_id != session.session_id or owner.control is not None or session._compacting):
            return False
        agent, branch, model = session.agent, session.session_data.active_branch, session.model
        revision = owner.navigation.revision if owner.navigation is not None else None
        tool_revision, permission_mode = agent.tool_selection_revision, session.permissions.mode

        def alive() -> bool:
            return (not owner.closed and self.enabled and generation == self.generation and self.producer is task
                and owner.session is session and session.agent is agent and session.session_id == scope[0]
                and session.session_data.active_branch == branch and session.model == model
                and owner.navigation is not None and owner.navigation.revision == revision
                and agent.tool_selection_revision == tool_revision and session.permissions.mode == permission_mode
                and not session.children.supervisor.active and not session.permissions.has_pending_requests)

        async def ask(name: str, projected: dict[str, Any]) -> Any:
            return await session.extension_dialogs.ask(name,
                {**projected, 'compaction_after_run_generation': generation}) if alive() else False

        return await self.queue.reviewed(extension, dialog, producer=task, ask=ask, alive=alive,
            settled=lambda: task.done() and not agent.is_streaming
                and (agent._producer_task is None or agent._producer_task.done()),
            successful=lambda: self.success and not task.cancelled() and task.exception() is None)

    async def close(self) -> None:
        self.negotiate(False)
        for job in tuple(self.jobs):
            job.cancel()
        await asyncio.gather(*tuple(self.jobs), return_exceptions=True)
