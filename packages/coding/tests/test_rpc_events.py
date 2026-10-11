"""Transient sources are opt-in and revoked independently of retained transcript access."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.agent.core import AgentToolResult
from xdog.agent.events import (
    AgentEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from xdog.ai.types import AssistantMessage, ImageContent, TextContent, ThinkingContent
from xdog.coding.modes.rpc.events import MAX_EVENT_BYTES, RpcEvents

from packages.coding.tests.test_rpc_transcripts import close, setup


@pytest.mark.parametrize('change', ['none', 'withdraw', 'revision', 'close'])
async def test_parent_transient_progress_lease_redaction_and_late_delivery(tmp_path, change):
    session, _, calls, nav, sources, events = setup(tmp_path)
    stream = RpcEvents(session, nav, events.append)
    try:
        stream.negotiate({'transient_events': 1})
        bound, lease = stream.binding, stream.lease
        assert bound is not None
        # Actual subscribed callback sees these lifecycle events; no manual wire fabrication.
        session.agent._emit(ToolExecutionStartEvent(tool_call_id='call', tool_name='tool',
                                                  args={'api_key': 'PRIVATE'}))
        session.agent._emit(ToolExecutionUpdateEvent(tool_call_id='call', tool_name='tool',
            partial_result=AgentToolResult(content=(TextContent(text='token=PRIVATE progress'),
                                                   ImageContent(data='PRIVATE-BINARY', mime_type='image/png')))))
        session.agent._emit(MessageUpdateEvent(message=AssistantMessage(content=(
            TextContent(text='VISIBLE'), ThinkingContent(thinking='PRIVATE-REDACTED', redacted=True)))))
        session.agent._emit(ToolExecutionEndEvent(tool_call_id='call', tool_name='tool', is_error=True))
        session.agent._emit(AgentEndEvent())
        if change == 'withdraw':
            stream.negotiate({'transient_events': 0})
        elif change == 'revision':
            nav.refresh_revision()
        elif change == 'close':
            stream.close()
        await asyncio.sleep(0)
        records = [event for event in events if event['type'] == 'transient_event']
        if change == 'none':
            assert len(records) == 5
            assert [event['sequence'] for event in records] == [1, 2, 3, 4, 5]
            assert all(event['event_owner'] == lease and event['retained'] is False for event in records)
            assert records[1]['data']['binary_omitted'] == 1 and records[3]['data']['is_error']
            assert 'PRIVATE' not in json.dumps(records) and 'VISIBLE' in json.dumps(records)
        else:
            assert not records
        assert not calls and session.permissions.mode == 'deny'
    finally:
        stream.close()
        await close(session, nav, sources)


@pytest.mark.parametrize('value', [True, False, '1', None, 2])
async def test_transient_capability_refuses_coercion_and_does_not_grant_history(tmp_path, value):
    session, _, _, nav, sources, events = setup(tmp_path)
    stream = RpcEvents(session, nav, events.append)
    try:
        with pytest.raises(ValueError):
            stream.negotiate({'transient_events': value})
        assert not stream.enabled and not sources.canonical_enabled
    finally:
        stream.close()
        await close(session, nav, sources)


async def test_transient_capacity_is_explicit_not_silent_truncation_and_reenable_has_new_lease(tmp_path):
    session, _, _, nav, sources, events = setup(tmp_path)
    stream = RpcEvents(session, nav, events.append)
    try:
        stream.negotiate({'transient_events': 1})
        old = stream.lease
        session.agent._emit(ToolExecutionStartEvent(tool_call_id='call', tool_name='tool', args={'text': 'x' * 100000}))
        await asyncio.sleep(0)
        event = events[-1]
        assert event['limited'] is True and event['data'] == {}
        assert len(json.dumps(event).encode()) <= MAX_EVENT_BYTES
        stream.negotiate({'transient_events': 0})
        stream.negotiate({'transient_events': 1})
        assert stream.lease != old
        stream._deliver((session.session_id, nav.revision, session.agent), old,
                        {'event': 'tool_execution_start', 'data': {'secret': 'PRIVATE'}})
        assert events[-1]['type'] == 'transient_capabilities'
    finally:
        stream.close()
        await close(session, nav, sources)


async def test_transient_scheduled_queue_is_bounded_and_overflow_is_visible(tmp_path):
    session, _, _, nav, sources, events = setup(tmp_path)
    stream = RpcEvents(session, nav, events.append)
    try:
        stream.negotiate({'transient_events': 1})
        for _ in range(100):
            session.agent._emit(ToolExecutionUpdateEvent(tool_call_id='call', tool_name='tool'))
        assert stream._pending == 32
        await asyncio.sleep(0)
        records = [event for event in events if event['type'] == 'transient_event']
        assert len(records) == 32 and any(event.get('limited') for event in records)
        assert stream._pending == 0
    finally:
        stream.close()
        await close(session, nav, sources)


def test_actual_cli_transient_partial_lease_withdrawal_and_abort_cleanup(tmp_path, monkeypatch):
    from packages.coding.tests.test_rpc_children_cli import Peer
    monkeypatch.setenv('XDOG_RPC_STREAM_PARTIAL', '1')
    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'transient_events': 1})
        caps = peer.read('transient_capabilities')
        assert caps['transient_events'] == 1 and caps['replay'] is False
        peer.send({'type': 'prompt', 'message': 'hold'})
        event = peer.read('transient_event')
        while event['event'] != 'message_update':
            event = peer.read('transient_event')
        assert event['event'] == 'message_update' and event['event_owner'] == caps['event_owner']
        assert event['session_id'] == peer.ready['session_id'] and event['retained'] is False
        assert 'RPC-LIVE-PARTIAL' in json.dumps(event)
        peer.send({'type': 'rpc_hello', 'transient_events': 0})
        assert peer.read('transient_capabilities')['transient_events'] == 0
        peer.send({'type': 'abort'})
        assert peer.read('ok')['action'] == 'abort'
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=5) == 0
        assert peer.marker.with_suffix('.settled').exists()
    finally:
        peer.close()


async def test_actual_parent_tool_loop_publishes_start_progress_end_without_second_loop(tmp_path):
    from xdog.agent import AgentTool
    from xdog.ai.types import ToolCall
    from xdog.ai.utils.event_stream import EventStream

    session, _, calls, nav, sources, events = setup(tmp_path)
    stream = RpcEvents(session, nav, events.append)
    executed = []
    async def execute(identifier, args, cancel=None, on_update=None, **kwargs):
        executed.append(identifier)
        await on_update(AgentToolResult(content=(TextContent(text='REAL-TOOL-PROGRESS password=PRIVATE'),)))
        return AgentToolResult(content=(TextContent(text='REAL-TOOL-FINAL'),))
    def adapter(model, context, options):
        message = (AssistantMessage(content=(ToolCall(id='progress-call', name='fixture_progress', arguments={}),))
                   if context.messages[-1].role == 'user' else
                   AssistantMessage(content=(TextContent(text='DONE'),)))
        return EventStream.empty(message)
    session.agent.set_stream_fn(adapter)
    session.agent.set_tools((*session.agent.state.tools, AgentTool(name='fixture_progress',
        parameters={'type': 'object'}, execute=execute)))
    session.permissions.set_mode('allow-all')
    try:
        stream.negotiate({'transient_events': 1})
        await session.send_message('work')
        await asyncio.sleep(0)
        records = [event for event in events if event['type'] == 'transient_event']
        assert [event['event'] for event in records if event['event'].startswith('tool_execution')] == [
            'tool_execution_start', 'tool_execution_update', 'tool_execution_end']
        assert 'REAL-TOOL-PROGRESS' in json.dumps(records) and 'PRIVATE' not in json.dumps(records)
        assert executed == ['progress-call'] and 'REAL-TOOL-FINAL' in str(session.messages)
        assert not calls  # Selected offline adapter only, never the original provider.
        assert session.permissions.mode == 'allow-all'
    finally:
        stream.close()
        await close(session, nav, sources)


def test_every_declared_agent_event_has_a_named_transient_projection_with_safe_message_boundaries():
    from typing import get_args

    from xdog.agent import AgentEvent
    from xdog.agent.events import (
        InputConsumedEvent,
        InputHandledEvent,
        InputRejectedEvent,
        InputTransformedEvent,
        MessageEndEvent,
    )
    from xdog.agent.input_queue import QueuedInput
    from xdog.ai.types import TextDeltaEvent, ToolCall, ToolResultMessage, Usage, UserMessage
    from xdog.coding.modes.rpc.events import _payload

    picture = ImageContent(data='PRIVATE-BINARY', mime_type='image/png')
    user = UserMessage(content=(TextContent(text='token=PRIVATE-CREDENTIAL'), picture))
    assistant = AssistantMessage(content=(TextContent(text='PUBLIC', text_signature='PRIVATE-SIGNATURE'),
        ThinkingContent(thinking='PRIVATE-REDACTED', redacted=True),
        ToolCall(id='call', name='tool', arguments={'password': 'PRIVATE-CREDENTIAL'},
                 thought_signature='PRIVATE-SIGNATURE')), usage=Usage(total_tokens=123))
    required = {
        InputConsumedEvent: {'input': QueuedInput('input', 'token=PRIVATE-CREDENTIAL', 'steer', images=(picture,))},
        InputHandledEvent: {'original': user, 'source': 'prompt', 'feedback': 'token=PRIVATE-CREDENTIAL'},
        InputRejectedEvent: {'original': user, 'source': 'prompt'},
        InputTransformedEvent: {'original': user, 'message': user, 'source': 'prompt'},
    }
    projected = []
    for event_class in get_args(AgentEvent):
        fields = required.get(event_class, {})
        if event_class.__name__ in {'MessageStartEvent', 'MessageUpdateEvent', 'MessageEndEvent', 'TurnEndEvent'}:
            fields = {'message': assistant}
        if event_class is MessageUpdateEvent:
            fields = {**fields, 'assistant_message_event': TextDeltaEvent(delta='PRIVATE-RAW-DELTA', index=2)}
        event = event_class(**fields)
        payload = _payload(event)
        assert payload is not None and payload['event'] == event.type
        projected.append(payload)
    assert len(projected) == 14
    encoded = json.dumps(projected)
    assert 'PRIVATE' not in encoded and 'PUBLIC' in encoded and '123' in encoded
    update = next(row for row in projected if row['event'] == 'message_update')
    assert update['data']['stream_kind'] == 'text_delta' and update['data']['stream_index'] == 2
    result = ToolExecutionEndEvent(result=AgentToolResult(content=(TextContent(text='PUBLIC-FINAL'), picture),
                                                        details={'api_key': 'PRIVATE-CREDENTIAL'}))
    final = _payload(result)
    assert final['data']['binary_omitted'] == 1 and 'PUBLIC-FINAL' in json.dumps(final)
    assert 'PRIVATE' not in json.dumps(final)
    tool_message = ToolResultMessage(tool_call_id='call', tool_name='tool', content=(picture,))
    assert 'PRIVATE-BINARY' not in json.dumps(_payload(MessageEndEvent(message=tool_message)))
