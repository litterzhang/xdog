"""Actual child event-loop progress cannot borrow parent UI, tool or transcript grants."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
from xdog.agent import AgentTool, AgentToolResult
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream

from packages.coding.tests.test_rpc_children import close, control, setup, wait


@pytest.mark.parametrize('enabled', [False, True])
async def test_real_child_tool_loop_progress_end_and_original_hook_are_owned(tmp_path, enabled):
    session, _, nav, sources, children, events = setup(tmp_path, 'allow-all')
    entered, release = threading.Event(), threading.Event()
    executed, original_events = [], []
    # Rebind through actual host machinery with a pre-existing session extension hook.
    children._detach()
    previous = session.children.supervisor.on_event
    async def original(spec, event):
        original_events.append(event.type)
        if previous is not None:
            await previous(spec, event)
    session.children.supervisor.on_event = original
    children.negotiate({'child_controls': 1, 'child_transient_events': int(enabled)})
    async def execute(identifier, args, cancel=None, on_update=None, **kwargs):
        executed.append(identifier)
        await on_update(AgentToolResult(content=(TextContent(text='CHILD-PROGRESS token=PRIVATE'),)))
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.003)
        return AgentToolResult(content=(TextContent(text='CHILD-FINAL'),))
    def adapter(model, context, options):
        return EventStream.empty(AssistantMessage(content=(
            ToolCall(id='child-call', name='fixture_progress', arguments={}),))
            if context.messages[-1].role == 'user' else AssistantMessage(content=(TextContent(text='DONE'),)))
    session.agent.set_stream_fn(adapter)
    session.agent.set_tools((*session.agent.state.tools,
        AgentTool(name='fixture_progress', parameters={'type': 'object'}, execute=execute)))
    try:
        await control(children, 'start', task='work', access='inherit')
        started = next(event for event in events if event['type'] == 'child_result')
        child = started['child_id']
        await wait(entered.is_set)
        if enabled:
            await wait(lambda: any(event['type'] == 'child_transient_event'
                                  and event['event'] == 'tool_execution_update' for event in events))
            record = next(event for event in events if event['type'] == 'child_transient_event'
                          and event['event'] == 'tool_execution_update')
            assert record['child_id'] == child and record['child_owner'] == children.lease
            assert record['event_generation'] == children.transient_generation and record['retained'] is False
            assert 'CHILD-PROGRESS' in json.dumps(record) and 'PRIVATE' not in json.dumps(record)
        else:
            assert not any(event['type'] == 'child_transient_event' for event in events)
        release.set()
        await control(children, 'wait', 'wait', child_id=child)
        await asyncio.sleep(0)
        assert executed == ['child-call'] and 'tool_execution_update' in original_events
        if enabled:
            records = [event for event in events if event['type'] == 'child_transient_event']
            assert [event['event'] for event in records if event['event'].startswith('tool_execution')] == [
                'tool_execution_start', 'tool_execution_update', 'tool_execution_end']
            assert [record['sequence'] for record in records] == sorted(record['sequence'] for record in records)
        assert not session.messages and session.permissions.mode == 'allow-all'
    finally:
        release.set()
        await close(session, nav, sources, children)
    assert session.children.supervisor.on_event is original


@pytest.mark.parametrize('change', ['withdraw', 're-enable', 'revision', 'controls'])
async def test_queued_child_transient_delivery_is_revoked_by_captured_generation_and_owner(tmp_path, change):
    session, _, nav, sources, children, events = setup(tmp_path)
    try:
        children.negotiate({'child_transient_events': 1})
        await control(children, 'start', task='hold')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        generation, owner, binding = children.transient_generation, children.bound, children.binding
        # Scheduling window after projection but before loop delivery.
        children._transient_pending += 1
        if change == 'withdraw':
            children.negotiate({'child_transient_events': 0})
        elif change == 're-enable':
            children.negotiate({'child_transient_events': 0})
            children.negotiate({'child_transient_events': 1})
        elif change == 'revision':
            nav.refresh_revision()
        else:
            children.negotiate({'child_controls': 0})
        children._deliver_transient(owner, binding, generation, child,
                                    {'event': 'tool_execution_update', 'data': {'text': 'OLD'}})
        assert not any(event['type'] == 'child_transient_event' and 'OLD' in str(event) for event in events)
    finally:
        await close(session, nav, sources, children)


async def test_child_event_delayed_by_original_extension_hook_cannot_reappear_after_reenable(tmp_path):
    from xdog.agent.events import ToolExecutionUpdateEvent
    from xdog.agent.supervision import ChildSpec

    session, _, nav, sources, children, events = setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    children._detach()
    async def original(spec, event):
        entered.set()
        await release.wait()
    session.children.supervisor.on_event = original
    children.negotiate({'child_controls': 1, 'child_transient_events': 1})
    task = None
    try:
        # Exercise the actual chained hook boundary with a held existing hook.
        hook = session.children.supervisor.on_event
        task = asyncio.create_task(hook(ChildSpec(id='a' * 12, task='work'), ToolExecutionUpdateEvent()))
        await entered.wait()
        children.negotiate({'child_transient_events': 0})
        children.negotiate({'child_transient_events': 1})
        release.set()
        await task
        await asyncio.sleep(0)
        assert children._transient_pending == 0
        assert not any(event['type'] == 'child_transient_event' for event in events)
    finally:
        release.set()
        if task is not None:
            await task
        await close(session, nav, sources, children)
