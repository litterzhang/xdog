"""One RPC projection per controller batch; input receipts remain independent."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from xdog.agent import (
    InputHandledEvent,
    MessageEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from xdog.agent.session_input import input_data
from xdog.ai.types import AssistantMessage, TextContent, ThinkingContent, ToolCall
from xdog.coding.core.sessions.batches import TurnBatch
from xdog.coding.core.sessions.inbox_models import InboxRecord
from xdog.coding.core.sessions.inbox_views import summary, visible
from xdog.coding.core.sessions.models import InputRecord, SessionEvent, TurnResult


def content_data(message: AssistantMessage | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for part in message.content if message is not None else ():
        if isinstance(part, TextContent):
            result.append({'type': 'text', 'text': part.text})
        elif isinstance(part, ThinkingContent) and not part.redacted:
            result.append({'type': 'thinking', 'thinking': part.thinking})
        elif isinstance(part, ToolCall):
            result.append({'type': 'tool_call', 'name': part.name, 'arguments': part.arguments})
    return result


@dataclass
class _BatchOutput:
    batch: TurnBatch
    remaining: set[str]
    producer: asyncio.Task[Any] | None
    handled: bool = False
    sequence: int = 0
    partial: AssistantMessage | None = None

    def identity(self) -> dict[str, Any]:
        return {'session_id': self.batch.session_id, 'generation': self.batch.generation,
                'batch_id': self.batch.id, 'request_ids': list(self.batch.request_ids)}


class RpcSessionEvents:
    def __init__(self, session: Any, runtime: Any, extensions: Any, emit: Any) -> None:
        self.session, self.runtime, self.extensions, self.emit = session, runtime, extensions, emit
        self.extensions.batch_stream = True
        self.scopes: dict[str, Any] = {}
        self.batches: dict[str, _BatchOutput] = {}
        self._failed: set[str] = set()
        self._ready = False
        self._initial_inbox: SessionEvent | None = None
        self.unsubscribe = runtime.controller.subscribe(self.receive)
        runtime.controller.before_batch = self.prepare
        runtime.controller.after_batch = self.finished

    async def prepare(self, batch: TurnBatch) -> None:
        state = _BatchOutput(batch, set(batch.request_ids), asyncio.current_task())
        self.batches[batch.id] = state
        self.emit({'type': 'message_start', **state.identity()})
        if self.extensions.ui_enabled:
            manager = self.extensions.bind('batch-' + batch.id)
            await manager.__aenter__()
            self.scopes[batch.id] = manager
        producer = asyncio.current_task()
        token = 'rpc-' + batch.id
        self.runtime.controller.hold_producer(token)

        async def settled() -> None:
            try:
                if producer is not None:
                    await asyncio.shield(asyncio.gather(producer, return_exceptions=True))
                await asyncio.sleep(0)  # Producer callbacks publish their reviewed follow-up jobs.
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

    async def finished(self, batch: TurnBatch, result: TurnResult) -> None:
        manager = self.scopes.pop(batch.id, None)
        if manager is not None:
            # The durable input_completed receipt, not this pre-persistence hook,
            # publishes the final outcome to after-run producers and the client.
            await manager.__aexit__(None, None, None)

    def ready(self) -> None:
        self._ready = True
        event, self._initial_inbox = self._initial_inbox, None
        if event is not None:
            self.receive(event)

    def _agent(self, event: SessionEvent, state: _BatchOutput) -> None:
        data = event.data
        sequence = getattr(data, 'sequence', 0)
        if sequence and sequence <= state.sequence:
            return
        state.sequence = sequence or state.sequence
        identity = state.identity()
        if isinstance(data, (MessageUpdateEvent, MessageEndEvent)) and isinstance(data.message, AssistantMessage):
            state.partial = data.message
        if isinstance(data, InputHandledEvent):
            state.handled = True
            self.emit({'type': 'input_handled', 'feedback': data.feedback, 'source': data.source,
                       'id': data.input_id, **identity})
        elif isinstance(data, MessageUpdateEvent) and isinstance(data.message, AssistantMessage):
            for part in data.message.content:
                if isinstance(part, TextContent):
                    self.emit({'type': 'text_delta', 'text': part.text, **identity})
                elif isinstance(part, ThinkingContent) and not part.redacted:
                    self.emit({'type': 'thinking_delta', 'text': part.thinking, **identity})
        elif isinstance(data, ToolExecutionStartEvent):
            self.emit({'type': 'tool_call', 'id': data.tool_call_id, 'name': data.tool_name,
                       'arguments': data.args, **identity})
        elif isinstance(data, (ToolExecutionEndEvent, ToolExecutionUpdateEvent)):
            result = data.result if isinstance(data, ToolExecutionEndEvent) else data.partial_result
            text = ''.join(part.text for part in result.content if isinstance(part, TextContent)) if result else ''
            self.emit({'type': 'tool_result' if isinstance(data, ToolExecutionEndEvent) else 'tool_update',
                       'id': data.tool_call_id, 'name': data.tool_name, 'text': text,
                       **({'is_error': data.is_error} if isinstance(data, ToolExecutionEndEvent) else {}), **identity})

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
                       'messages': [summary(row,current_branch=self.runtime.controller.branch) for row in rows],
                       'untrusted': True})
            return
        if event.kind == 'inbox_error':
            self.emit({'type': 'inbox_error', 'session_id': event.session_id, 'generation': event.generation,
                       'message': str(event.data)})
            return
        state = self.batches.get(event.batch_id)
        if event.kind == 'agent_event':
            if state is not None:
                self._agent(event, state)
            return
        identity = {'session_id': event.session_id, 'generation': event.generation,
                    'batch_id': event.batch_id, 'request_ids': list(event.request_ids)}
        data: dict[str, Any] = {}
        if isinstance(event.data, InputRecord):
            data = {'input': input_data(event.data.input), 'status': event.data.status}
        elif isinstance(event.data, TurnResult):
            data = {'status': event.data.status, 'error': event.data.error}
        self.emit({'type': 'session_event', 'request_id': event.request_id, 'event': event.kind, **identity, **data})
        if event.kind != 'input_completed' or not isinstance(event.data, TurnResult):
            return
        result = event.data
        if state is None:
            key = event.batch_id or event.request_id
            if result.status != 'succeeded' and key not in self._failed:
                self._failed.add(key)
                self.emit({'type': 'error', 'message': result.error or 'Interrupted', **identity})
            return
        state.remaining.discard(event.request_id)
        if state.remaining:
            return
        self.batches.pop(event.batch_id)
        if self.extensions.after_run_compaction.producer is state.producer:
            self.extensions.after_run_compaction.success = result.status == 'succeeded'
        error = result.error or 'Interrupted' if result.status != 'succeeded' else ''
        self.emit({'type': 'message_end', 'content': content_data(result.response or state.partial),
                   'status': result.status, **state.identity(),
                   **({'input_handled': True} if state.handled else {}),
                   **({'is_error': True} if error else {})})
        if error:
            self.emit({'type': 'error', 'message': error, **state.identity()})

    def close(self) -> None:
        self.extensions.batch_stream = False
        self.unsubscribe()
