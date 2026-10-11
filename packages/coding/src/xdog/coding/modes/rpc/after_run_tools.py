"""Independent RPC after-run tool selection; ordinary idle durable CAS, no new model loop."""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from xdog.agent.extensions.active_tools import snapshot
from xdog.agent.extensions.active_tools_request import AfterRunToolSelections
from xdog.agent.extensions.invocation import current_invocation
from xdog.agent.extensions.runtime import _owned_disk


class RpcAfterRunTools:
    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.enabled = False
        self.generation = 0
        self.queue = AfterRunToolSelections(lambda event: owner.loop.call_soon_threadsafe(owner.emit, event))
        self.jobs: set[asyncio.Task[None]] = set()

    @property
    def busy(self) -> bool:
        return any(not task.done() for task in self.jobs)

    def negotiate(self, enabled: bool) -> None:
        if self.enabled != enabled:
            self.generation += 1
            self.queue.discard('capability-changed')
        self.enabled = enabled
        broker = getattr(self.owner.session, 'extension_dialogs', None)
        if not enabled and broker is not None:
            broker.cancel_where(
                lambda row: row.get('operation') == 'select_tools' and row.get('after_run') is True)
        if self.owner.scope is not None:
            self.owner.session.extensions.active_tools_request_handler = self.ask if enabled else None

    def bind(self) -> None:
        self.queue.discard('new-producer')
        task = self.owner.after_run_compaction.producer  # Same observation even if compaction is not enabled.
        if task is not None:
            task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[Any]) -> None:
        if not self.enabled or self.queue._pending is None:
            return  # A negotiated but unused feature must not create a spurious busy window.
        async def drain() -> None:
            owner = self.owner
            producer = owner.session.agent._producer_task
            if producer is not None and not producer.done():
                await asyncio.shield(asyncio.gather(producer, return_exceptions=True))
            if (owner.closed or owner.scope is not None or owner.navigation is None or owner.navigation.busy
                    or owner.after_run_compaction.producer is not task):
                self.queue.discard('owner-changed')
                return
            item = self.queue.take()
            if item is None:
                return
            committed = threading.Event()
            failed = False
            def select() -> None:
                owner.session.select_active_tools(item.names, expected_revision=item.revision, alive=item.alive)
                committed.set()
            try:
                await _owned_disk(select)
            except (asyncio.CancelledError, ValueError, RuntimeError, OSError):
                failed = True
            if not owner.closed:
                owner.emit({'type': 'extension_active_tools_after_run_result', 'request_id': item.id,
                    'session_id': item.session_id, 'applied': committed.is_set(),
                    'effects_uncertain': failed and not committed.is_set(),
                    'notice': 'Selection confirmed only if applied; otherwise inspect configuration. '
                              'Permissions unchanged; no replay or implied rollback'})
        job = asyncio.create_task(drain())
        self.jobs.add(job)
        def done(work: asyncio.Task[None]) -> None:
            self.jobs.discard(work)
            if not work.cancelled():
                work.exception()
        job.add_done_callback(done)

    async def ask(self, extension: str, dialog: dict[str, Any]) -> bool:
        owner, generation = self.owner, self.generation
        task, scope = owner.after_run_compaction.producer, owner.scope
        lease = current_invocation()
        if (not self.enabled or task is None or scope is None or task.done() or owner.control is not None
                or lease is None or lease.child_id or lease.session_id != owner.session.session_id):
            return False
        session, agent = owner.session, owner.session.agent
        current = snapshot(agent, session.session_id)
        revision = owner.navigation.revision if owner.navigation is not None else None
        def alive() -> bool:
            return (not owner.closed and self.enabled and generation == self.generation
                and owner.after_run_compaction.producer is task and owner.session is session
                and session.agent is agent and session.session_id == scope[0]
                and owner.navigation is not None and owner.navigation.revision == revision
                and agent.tool_selection_revision == current['revision']
                and not session.children.supervisor.active and not session.permissions.has_pending_requests)
        async def ask(name: str, projected: dict[str, Any]) -> Any:
            return await session.extension_dialogs.ask(name,
                {**projected, 'active_tools_after_run_generation': generation}) if alive() else False
        return await self.queue.reviewed(extension, dialog, producer=task, ask=ask, current=current, alive=alive,
            settled=lambda: task.done() and not agent.is_streaming
                and (agent._producer_task is None or agent._producer_task.done()),
            successful=lambda: owner.after_run_compaction.success and not task.cancelled() and task.exception() is None)

    def abort(self) -> None:
        self.generation += 1
        self.queue.discard('aborted')
        for job in tuple(self.jobs):
            job.cancel()

    async def close(self) -> None:
        self.negotiate(False)
        for job in tuple(self.jobs):
            job.cancel()
        await asyncio.gather(*tuple(self.jobs), return_exceptions=True)
