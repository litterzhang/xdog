"""RPC observes the shared owner; it does not own an external-event execution loop."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from xdog.agent.events import EventMetadata
from xdog.agent.session_input import SessionInput, input_data
from xdog.coding.core.sessions.inbox_models import InboxRecord
from xdog.coding.core.sessions.inbox_views import summary, visible
from xdog.coding.core.sessions.models import InputRecord, SessionEvent, TurnResult


class RpcSessionEvents:
    def __init__(self, session: Any, runtime: Any, extensions: Any, emit: Any) -> None:
        self.session, self.runtime, self.extensions, self.emit = session, runtime, extensions, emit
        self.scopes: dict[str, Any] = {}
        self.external: set[str] = set()
        self._ready = False
        self._initial_inbox: SessionEvent | None = None
        self.unsubscribe = runtime.controller.subscribe(self.receive)
        runtime.controller.before_turn = self.prepare
        runtime.controller.after_turn = self.finished

    async def prepare(self, value: SessionInput) -> None:
        if value.origin != 'human' and self.extensions.ui_enabled:
            manager = self.extensions.bind('acp-' + value.id)
            await manager.__aenter__()
            self.scopes[value.id] = manager
        producer = self.extensions.after_run_compaction.producer or asyncio.current_task()
        token = 'rpc-' + value.id
        self.runtime.controller.hold_producer(token)
        async def settled() -> None:
            try:
                if producer is not None:
                    await asyncio.shield(asyncio.gather(producer, return_exceptions=True))
                await asyncio.sleep(0)  # Producer done callbacks publish their reviewed follow-up jobs.
                for owner in (self.extensions.after_run_compaction, self.extensions.after_run_name,
                              self.extensions.after_run_tools):
                    if owner.jobs:
                        await asyncio.gather(*tuple(owner.jobs), return_exceptions=True)
                for task in (getattr(self.extensions.navigation, 'task', None), self.extensions.task):
                    if task is not None and task is not producer:
                        await asyncio.gather(task, return_exceptions=True)
            finally:
                self.runtime.controller.release_producer(token)
        task = asyncio.create_task(settled())
        self.runtime.producer_tasks.add(task)
        task.add_done_callback(self.runtime.producer_tasks.discard)

    async def finished(self, value: SessionInput, result: TurnResult) -> None:
        manager = self.scopes.pop(value.id, None)
        if manager is not None:
            from xdog.coding.modes.rpc.after_run_compaction import producer_outcome
            producer_outcome(result.status == 'succeeded')
            await manager.__aexit__(None, None, None)

    def ready(self) -> None:
        self._ready = True
        event, self._initial_inbox = self._initial_inbox, None
        if event is not None:
            self.receive(event)

    def receive(self, event: SessionEvent) -> None:
        if event.session_id != self.session.session_id or event.generation != self.runtime.controller.generation:
            return
        if event.kind == 'inbox_snapshot' and isinstance(event.data, tuple):
            if not all(isinstance(row, InboxRecord) for row in event.data):
                return
            if not self._ready:
                self._initial_inbox = event
                return
            rows = visible(event.data, self.runtime.controller.store.records())
            self.emit({'type': 'inbox_snapshot', 'session_id': event.session_id, 'generation': event.generation,
                       'messages': [summary(row) for row in rows], 'untrusted': True})
            return
        if event.kind == 'inbox_error':
            self.emit({'type': 'inbox_error', 'session_id': event.session_id, 'generation': event.generation,
                       'message': str(event.data)})
            return
        if isinstance(event.data, InputRecord):
            if event.data.input.origin == 'human':
                return
            self.external.add(event.request_id)
            data = {'input': input_data(event.data.input), 'status': event.data.status}
        elif event.request_id not in self.external:
            return
        elif event.kind == 'agent_event' and isinstance(event.data, EventMetadata):
            data = {'event': asdict(event.data)}
        elif isinstance(event.data, TurnResult):
            data = {'status': event.data.status, 'error': event.data.error}
        else:
            data = {}
        self.emit({'type': 'session_event', 'session_id': event.session_id,
                   'request_id': event.request_id, 'event': event.kind, **data})
        if event.kind == 'input_completed':
            self.external.discard(event.request_id)

    def close(self) -> None:
        self.unsubscribe()
