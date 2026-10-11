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
    InputConsumedEvent,
    InputHandledEvent,
    InputRejectedEvent,
    InputTransformedEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
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
                    payload = {'event': event.type, 'data': {}, 'limited': True}
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


def _payload(event: AgentEvent) -> dict[str, Any] | None:
    data: dict[str, Any]
    if isinstance(event, ToolExecutionStartEvent):
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
    return {'event': event.type, 'data': data}
