"""Transient sources are opt-in and revoked independently of retained transcript access."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.agent.core import AgentToolResult
from xdog.agent.events import (
    AgentEndEvent,
    CompactionEndEvent,
    CompactionFailedEvent,
    CompactionStartEvent,
    MessageUpdateEvent,
    SnapshotEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    UsageEvent,
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
    assert len(projected) == len(get_args(AgentEvent))
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


def test_sdk_observation_projections_have_exact_safe_fields_and_separate_identity():
    from xdog.ai.types import CostBreakdown, Usage, UserMessage
    from xdog.coding.modes.rpc.events import _payload

    metadata = {'session_id': 'sdk-session', 'run_id': 'sdk-run', 'sequence': 41}
    usage = Usage(input=10, output=3, cache_read=2, cache_write=1, total_tokens=16,
                  cost=CostBreakdown(input=.1, output=.2, cache_read=.03, cache_write=.04, total=.37))
    for source in ('model', 'compaction'):
        assert _payload(UsageEvent(usage=usage, model='offline/model', source=source, **metadata)) == {
            'event': 'usage', 'sdk': metadata,
            'data': {'source': source, 'model': 'offline/model', 'usage': {
                'input': 10, 'output': 3, 'cache_read': 2, 'cache_write': 1, 'total_tokens': 16,
                'cost': {'input': .1, 'output': .2, 'cache_read': .03, 'cache_write': .04, 'total': .37},
            }},
        }
    secret = 'ARBITRARY-PRIVATE-CONTENT without a recognizable credential prefix'
    cases = (
        (CompactionStartEvent(automatic=True, **metadata), {'automatic': True}),
        (CompactionEndEvent(automatic=True, generation=2, summary=secret,
                            removed_messages=(UserMessage(content=secret),), **metadata),
         {'automatic': True, 'generation': 2, 'removed_message_count': 1, 'summary_omitted': True}),
        (CompactionFailedEvent(error=secret, **metadata), {'automatic': False, 'error_omitted': True}),
        (SnapshotEvent(snapshot_json=json.dumps({'messages': secret, 'api_key': secret}), **metadata),
         {'snapshot_omitted': True}),
        (SnapshotEvent(snapshot_json='NOT JSON ' + secret, **metadata), {'snapshot_omitted': True}),
    )
    for event, expected in cases:
        payload = _payload(event)
        assert payload == {'event': event.type, 'sdk': metadata, 'data': expected}
        assert 'PRIVATE' not in json.dumps(payload)
        assert all(key not in payload['data'] for key in ('removed_messages', 'snapshot_json', 'summary', 'error'))
    redacted = _payload(UsageEvent(model='token=PRIVATE-MODEL', session_id='api_key=PRIVATE-SESSION',
                                  run_id='password=PRIVATE-RUN', sequence=1))
    assert 'PRIVATE' not in json.dumps(redacted)


async def test_sdk_metadata_preserves_rpc_lease_sequence_and_snapshot_privacy(tmp_path):
    from xdog.ai.types import Usage, UserMessage

    session, _, calls, nav, sources, events = setup(tmp_path)
    stream = RpcEvents(session, nav, events.append)
    try:
        # An observation before negotiation advances only the SDK counter.
        session.agent._emit(AgentEndEvent())
        stream.negotiate({'transient_events': 1})
        session.agent._emit(AgentEndEvent())
        await asyncio.sleep(0)
        sdk_events = [
            session.agent._emit(UsageEvent(usage=Usage(input=17), model='offline/model')),
            session.agent._emit(CompactionEndEvent(summary='PRIVATE-SUMMARY',
                removed_messages=(UserMessage(content='PRIVATE-HISTORY'),), generation=3)),
            session.agent._emit(SnapshotEvent(snapshot_json='PRIVATE-INVALID-JSON')),
        ]
        await asyncio.sleep(0)
        rows = [row for row in events if row['type'] == 'transient_event'][1:]
        assert len(rows) == len(sdk_events) == 3
        for row, sdk_event in zip(rows, sdk_events, strict=True):
            assert row['session_id'] == session.session_id
            assert row['event_owner'] == stream.lease and row['retained'] is False
            assert row['sdk'] == {'session_id': sdk_event.session_id, 'run_id': sdk_event.run_id,
                                  'sequence': sdk_event.sequence}
        assert [row['sequence'] for row in rows] == [2, 3, 4]
        assert [row['sdk']['sequence'] for row in rows] == [3, 4, 5]
        assert 'PRIVATE' not in json.dumps(rows)
        assert not calls and session.permissions.mode == 'deny'
    finally:
        stream.close()
        await close(session, nav, sources)


async def test_oversized_usage_keeps_sdk_correlation_when_data_is_limited(tmp_path):
    session, _, _, nav, sources, events = setup(tmp_path)
    stream = RpcEvents(session, nav, events.append)
    try:
        stream.negotiate({'transient_events': 1})
        sdk_event = session.agent._emit(UsageEvent(model='x' * MAX_EVENT_BYTES))
        await asyncio.sleep(0)
        row = events[-1]
        assert row['event'] == 'usage' and row['limited'] is True and row['data'] == {}
        assert row['sdk'] == {'session_id': sdk_event.session_id, 'run_id': sdk_event.run_id,
                              'sequence': sdk_event.sequence}
        assert len(json.dumps(row).encode()) <= MAX_EVENT_BYTES
    finally:
        stream.close()
        await close(session, nav, sources)


@pytest.mark.parametrize('value', ['PRIVATE-INVALID-COUNTER', True, -1])
def test_usage_projection_rejects_non_numeric_or_negative_counters(value):
    from xdog.ai.types import Usage
    from xdog.coding.modes.rpc.events import _payload

    with pytest.raises(ValueError, match='Invalid SDK usage event'):
        _payload(UsageEvent(usage=Usage(input=value)))
