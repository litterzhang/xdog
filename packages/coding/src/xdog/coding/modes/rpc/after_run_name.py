"""Independent reviewed name CAS after original RPC/Agent producer settlement."""
from __future__ import annotations

import asyncio
from typing import Any

from xdog.agent.extensions.invocation import current_invocation
from xdog.agent.extensions.name_request import AfterRunNames


class RpcAfterRunName:
    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.enabled, self.generation = False, 0
        self.producer: asyncio.Task[Any] | None = None
        self.queue = AfterRunNames(lambda event: owner.loop.call_soon_threadsafe(owner.emit, event))
        self.jobs: set[asyncio.Task[None]] = set()
        self.execution: tuple[str, str] | None = None

    def negotiate(self, enabled: bool) -> None:
        self.enabled, self.generation = enabled and self.owner.navigation is not None, self.generation + 1
        self.queue.discard('capability-withdrawn')
        owner = self.owner
        broker = getattr(owner.session, 'extension_dialogs', None)
        if broker is not None:
            broker.cancel_where(lambda dialog: dialog.get('name_after_run') is True)
        if owner.scope is not None:
            owner.session.extensions.name_request_handler = self.ask if enabled else None
        if (self.execution is not None and owner.navigation is not None and owner.navigation.busy
                and owner.navigation.owner == self.execution):
            owner.navigation.cancel({'request_id': self.execution[0], 'expected_session': self.execution[1]})

    def bind(self) -> None:
        self.queue.discard('new-producer')
        self.producer = self.owner.after_run_compaction.producer
        if self.producer is not None:
            self.producer.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[Any]) -> None:
        if self.owner.closed or not self.enabled:
            return
        async def drain() -> None:
            if self.producer is not task:
                return
            owner, agent = self.owner, self.owner.session.agent
            if agent._producer_task is not None and not agent._producer_task.done():
                await asyncio.shield(asyncio.gather(agent._producer_task, return_exceptions=True))
            item = self.queue.take()
            if item is None:
                return
            if owner.navigation is None or owner.navigation.busy or owner.busy:
                owner.emit({'type': 'extension_name_after_run', 'request_id': item.id,
                            'session_id': item.session_id, 'status': 'discarded'})
                return
            try:
                owner.navigation.start({'action': 'rename', 'request_id': item.id, 'expected_session': item.session_id,
                    'session_revision': owner.navigation.revision, 'name': item.name,
                    'expected_name': item.expected_name},
                    turn_running=False, extension_running=False, name_alive=item.alive)
                self.execution = item.id, item.session_id
            except (ValueError, RuntimeError, OSError):
                owner.emit({'type': 'extension_name_after_run', 'request_id': item.id, 'status': 'discarded',
                            'session_id': item.session_id})
        job = asyncio.create_task(drain())
        self.jobs.add(job)
        def done(work: asyncio.Task[None]) -> None:
            self.jobs.discard(work)
            if not work.cancelled():
                work.exception()
        job.add_done_callback(done)

    async def ask(self, extension: str, dialog: dict[str, Any]) -> bool:
        owner, task, generation = self.owner, self.producer, self.generation
        lease, scope, session = current_invocation(), owner.scope, owner.session
        if not self.enabled or task is None or task.done() or lease is None or lease.child_id or scope is None:
            return False
        agent, branch = session.agent, session.session_data.active_branch
        def alive() -> bool:
            return (self.enabled and self.generation == generation and not owner.closed and self.producer is task
                and owner.after_run_compaction.producer is task and owner.session is session and session.agent is agent
                and session.session_id == lease.session_id and session.session_data.active_branch == branch
                and not session.children.supervisor.active and not session.permissions.has_pending_requests)
        async def review(name: str, projected: dict[str, Any]) -> Any:
            return await session.extension_dialogs.ask(name,
                {**projected, 'name_after_run_generation': generation}) if alive() else False
        return await self.queue.reviewed(extension, dialog, producer=task, ask=review, alive=alive,
            settled=lambda: task.done() and not agent.is_streaming
                and (agent._producer_task is None or agent._producer_task.done()),
            successful=lambda: owner.after_run_compaction.success and not task.cancelled() and task.exception() is None)

    async def close(self) -> None:
        self.negotiate(False)
        for job in tuple(self.jobs):
            job.cancel()
        await asyncio.gather(*tuple(self.jobs), return_exceptions=True)
