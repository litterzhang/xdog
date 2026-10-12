"""Opt-in connection/session-owned transient events, never retained-history replay."""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import Callable
from typing import Any

from xdog.agent import AgentEvent, AgentMessage
from xdog.agent.events import (
    AgentEndEvent,
    AgentStartEvent,
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
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
    UsageEvent,
)
from xdog.ai.types import ImageContent, TextContent
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.canonical_source import project, safe_json
from xdog.coding.modes.rpc.navigation import RpcNavigation

MAX_EVENT_BYTES = 64 * 1024


class RpcEvents:
    def __init__(self, session: AgentSession, navigation: RpcNavigation,
                 emit: Callable[[dict[str, Any]], None]) -> None:
        self.session, self.navigation, self.emit = session, navigation, emit
        self.loop = asyncio.get_running_loop()
        self.enabled = False
        self.closed = False
        self.lease = uuid.uuid4().hex
        self.sequence = 0
        self._pending = 0
        self._lock = threading.Lock()
        self._limited = False
        self.binding: tuple[str, str, Any] | None = None
        self.unsubscribe: Callable[[], None] | None = None

    def negotiate(self, packet: dict[str, Any]) -> None:
        if 'transient_events' not in packet:
            return
        value = packet['transient_events']
        if type(value) is not int or value not in (0, 1):
            raise ValueError('Transient events require exact integer 0 or 1')
        self._detach()
        self.enabled = value == 1
        self.lease = uuid.uuid4().hex
        self.bind()
        self.emit({'type': 'transient_capabilities', 'transient_events': int(self.enabled),
                   'session_id': self.session.session_id, 'session_revision': self.navigation.revision,
                   'event_owner': self.lease, 'max_event_bytes': MAX_EVENT_BYTES, 'replay': False})

    def bind(self) -> None:
        if self.closed or not self.enabled or not hasattr(self.session, 'agent'):
            return
        binding = self.session.session_id, self.navigation.revision, self.session.agent
        if self.binding == binding:
            return
        self._detach()
        self.binding = binding
        lease = self.lease
        def observe(event: AgentEvent) -> None:
            # Copy and redact while still at the owning agent boundary. Scheduled
            # delivery checks the original lease again, so withdrawal cannot leak late events.
            try:
                payload = _payload(event)
            except (ValueError, TypeError, RecursionError, UnicodeError):
                payload = {'event': event.type, 'data': {}, 'limited': True}
            if payload is not None:
                if len(json.dumps(payload, ensure_ascii=True, allow_nan=False).encode()) > MAX_EVENT_BYTES // 2:
                    payload = {**payload, 'data': {}, 'limited': True}
                with self._lock:
                    if self._pending >= 32:
                        self._limited = True
                        return
                    self._pending += 1
                self.loop.call_soon_threadsafe(self._scheduled, binding, lease, payload)
        self.unsubscribe = self.session.agent.subscribe(observe)

    def _scheduled(self, binding: tuple[str, str, Any], lease: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._pending -= 1
            limited, self._limited = self._limited, False
        if limited:
            payload = {**payload, 'limited': True}
        self._deliver(binding, lease, payload)

    def _deliver(self, binding: tuple[str, str, Any], lease: str, payload: dict[str, Any]) -> None:
        if (self.closed or not self.enabled or self.binding != binding or self.lease != lease
                or self.session.session_id != binding[0] or self.navigation.revision != binding[1]
                or self.session.agent is not binding[2]):
            return
        self.sequence += 1
        event = {'type': 'transient_event', 'session_id': binding[0], 'session_revision': binding[1],
                 'event_owner': lease, 'sequence': self.sequence, 'retained': False, **payload}
        if len(json.dumps(event, ensure_ascii=True, allow_nan=False).encode()) > MAX_EVENT_BYTES:
            # Never truncate structured content or silently present a partial payload as complete.
            event = {key: value for key, value in event.items() if key not in {'data'}}
            event.update(data={}, limited=True)
        self.emit(event)

    def _detach(self) -> None:
        if self.unsubscribe is not None:
            self.unsubscribe()
        self.unsubscribe, self.binding = None, None

    def close(self) -> None:
        self.closed = True
        self._detach()


def _message(message: AgentMessage | None) -> dict[str, Any] | None:
    if message is None:
        return None
    value = project((message,))[0]
    # Canonical export has separate binary authority. Transient events never
    # inherit it, including final messages, user inputs and tool results.
    for part in value.get('content', []):
        if part.get('type') == 'image':
            part.pop('data', None)
            part['binary_omitted'] = True
    return value


def _sdk_metadata(event: AgentEvent) -> dict[str, Any]:
    """SDK identity is separate from the RPC session lease/delivery sequence.

    Bound identity strings independently so payload-size fallback can retain
    correlation without retaining oversized data or unbounded identifiers.
    """
    if (any(not isinstance(value, str) or len(value) > 256 for value in (event.session_id, event.run_id))
            or type(event.sequence) is not int or event.sequence < 0):
        raise ValueError('Invalid SDK event identity')
    return {'session_id': safe_json(event.session_id), 'run_id': safe_json(event.run_id), 'sequence': event.sequence}


def _payload(event: AgentEvent) -> dict[str, Any] | None:
    data: dict[str, Any]
    if isinstance(event, (CompactionStartEvent, CompactionEndEvent, CompactionFailedEvent)):
        if type(event.automatic) is not bool:
            raise ValueError('Invalid SDK compaction metadata')
    if isinstance(event, CompactionEndEvent) and (type(event.generation) is not int or event.generation < 0):
        raise ValueError('Invalid SDK compaction generation')
    if isinstance(event, UsageEvent):
        # Do not recursively serialize SDK dataclasses or apply key-based token
        # redaction to numeric token counters. Explicit numeric fields only.
        counters = {key: getattr(event.usage, key) for key in
                    ('input', 'output', 'cache_read', 'cache_write', 'total_tokens')}
        cost = {key: getattr(event.usage.cost, key)
                for key in ('input', 'output', 'cache_read', 'cache_write', 'total')}
        if (any(type(value) is not int or value < 0 for value in counters.values())
                or any(type(value) not in (int, float) or value < 0 for value in cost.values())
                or event.source not in ('model', 'compaction') or not isinstance(event.model, str)):
            raise ValueError('Invalid SDK usage event')
        json.dumps(cost, allow_nan=False)
        data = {'source': event.source, 'model': safe_json(event.model), 'usage': {**counters, 'cost': cost}}
    elif isinstance(event, CompactionStartEvent):
        data = {'automatic': event.automatic}
    elif isinstance(event, CompactionEndEvent):
        data = {'automatic': event.automatic, 'generation': event.generation,
                'removed_message_count': len(event.removed_messages), 'summary_omitted': True}
    elif isinstance(event, CompactionFailedEvent):
        # Summary/error prose can contain arbitrary sensitive history, not merely
        # recognizable credential patterns. Keep observations metadata-only.
        data = {'automatic': event.automatic, 'error_omitted': True}
    elif isinstance(event, SnapshotEvent):
        # Do not even parse snapshot_json/access snapshot: opt-in transient
        # events grant neither canonical history nor persistence/credential data.
        data = {'snapshot_omitted': True}
    elif isinstance(event, ToolExecutionStartEvent):
        data = {'call_id': event.tool_call_id, 'tool': event.tool_name, 'arguments': safe_json(event.args)}
    elif isinstance(event, ToolExecutionUpdateEvent):
        result = event.partial_result
        data = {'call_id': event.tool_call_id, 'tool': event.tool_name,
                'text': safe_json('\n'.join(part.text for part in result.content if isinstance(part, TextContent)))
                if result is not None else '',
                'binary_omitted': sum(isinstance(part, ImageContent) for part in result.content)
                if result is not None else 0}
    elif isinstance(event, ToolExecutionEndEvent):
        result = event.result
        data = {'call_id': event.tool_call_id, 'tool': event.tool_name, 'is_error': event.is_error,
                'text': safe_json('\n'.join(part.text for part in result.content if isinstance(part, TextContent)))
                if result is not None else '', 'details': safe_json(result.details) if result is not None else None,
                'binary_omitted': sum(isinstance(part, ImageContent) for part in result.content)
                if result is not None else 0}
    elif isinstance(event, (MessageStartEvent, MessageUpdateEvent, MessageEndEvent)):
        data = {'message': _message(event.message)}
        if isinstance(event, MessageUpdateEvent) and event.assistant_message_event is not None:
            streamed = event.assistant_message_event
            # Raw deltas can expose fragmented credentials/signatures. Export the
            # already-redacted cumulative message and safe stream coordinates only.
            data['stream_kind'] = streamed.type
            index = getattr(streamed, 'index', None)
            if type(index) is int and index >= 0:
                data['stream_index'] = index
    elif isinstance(event, InputConsumedEvent):
        data = {'input_id': event.input.id, 'delivery': event.input.delivery,
                'selected': event.input.selected, 'text': safe_json(event.input.content),
                'binary_omitted': len(event.input.images)}
    elif isinstance(event, InputTransformedEvent):
        data = {'input_id': event.input_id, 'source': event.source, 'message': _message(event.message)}
    elif isinstance(event, InputRejectedEvent):
        data = {'input_id': event.input_id, 'source': event.source}
    elif isinstance(event, InputHandledEvent):
        data = {'feedback': safe_json(event.feedback), 'source': event.source, 'input_id': event.input_id}
    elif isinstance(event, TurnEndEvent):
        data = {'message': _message(event.message), 'tool_results': len(event.tool_results)}
    elif isinstance(event, (AgentStartEvent, TurnStartEvent, AgentEndEvent)):
        data = {}  # Never copy the whole canonical history into an agent-end event.
    else:
        return None
    return {'event': event.type, 'data': data, 'sdk': _sdk_metadata(event)}
