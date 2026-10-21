"""Settled child Agents are permanently closed without losing pending recovery."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent.child_queue import recovery_from_dicts
from xdog.ai.types import AssistantMessage, ImageContent, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream

from packages.claw.tests.test_child_runtime import application, prepared


@pytest.mark.parametrize('terminal', ['completed', 'cancel', 'aclose'])
async def test_retained_actual_child_agent_is_closed_after_settlement_and_cannot_request_again(tmp_path, terminal):
    calls, retained, order = [], [], []
    entered = asyncio.Event()
    def stream(model, context, options):
        calls.append(context)
        entered.set()
        if terminal == 'completed':
            return EventStream.empty(AssistantMessage(content=(TextContent(text='actual completed response'),)))
        async def blocked():
            try:
                yield StartEvent(partial=AssistantMessage(content=(TextContent(text='actual running child'),)))
                await asyncio.Event().wait()
            finally:
                order.append('provider-settled')
        return EventStream.from_async_generator(blocked())
    app = application(tmp_path, stream, tools=())
    try:
        session = await prepared(app)
        runtime = session.children.runtime
        original_factory, original_finalize = runtime.factory, runtime.finalize
        pending = []
        async def factory(spec, publish):
            agent = await original_factory(spec, publish)
            retained.append(agent)
            original_close = agent.aclose
            async def close():
                assert not agent.pending_inputs(), 'Agent close must not erase host-unrecorded inputs'
                assert runtime._children[spec.id].undelivered == tuple(pending)
                order.append('agent-close')
                await original_close()
                assert agent.closed
            agent.aclose = close
            return agent
        async def finalize(spec):
            assert retained[0].closed, 'Child extensions must finalize after the Agent is permanently closed'
            order.append('finalize')
            if original_finalize is not None:
                await original_finalize(spec)
        runtime.factory, runtime.finalize = factory, finalize
        identifier = await runtime.start('actual initial user task', config={'tools': []})
        async with asyncio.timeout(4):
            await entered.wait()
        if terminal != 'completed':
            image = ImageContent(data=('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE'
                                       'QVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), mime_type='image/png')
            await runtime.control('steer', identifier, 'PENDING STEER KNOWLEDGE', images=(image,))
            await runtime.control('follow-up', identifier, 'PENDING FOLLOW-UP KNOWLEDGE')
            pending[:] = retained[0].pending_inputs()
            assert len(pending) == 2
        if terminal == 'aclose':
            await runtime.aclose()
            record = runtime._record(runtime._children[identifier], transcript=True)
        else:
            record = await runtime.control('wait' if terminal == 'completed' else 'cancel', identifier)
        assert record['status'] == ('completed' if terminal == 'completed' else 'canceled'), record['error']
        assert retained[0].closed and order[-2:] == ['agent-close', 'finalize']
        assert runtime._children[identifier].agent is None
        assert not retained[0].pending_inputs()
        before = len(calls)
        with pytest.raises(RuntimeError, match='closing or closed'):
            await retained[0].prompt('must not revive this settled child Agent')
        assert len(calls) == before == 1
        saved = session.children.store.load()[0]
        assert saved['status'] == record['status']
        if pending:
            assert recovery_from_dicts(saved['undelivered_inputs']) == tuple(pending)
            assert recovery_from_dicts(record['undelivered_inputs']) == tuple(pending)
            assert 'PENDING STEER KNOWLEDGE' not in str(record['messages'])
            assert 'PENDING FOLLOW-UP KNOWLEDGE' not in str(record['messages'])
            assert 'provider-settled' in order
        assert not session.agent.closed  # A child settlement never closes its parent.
    finally:
        await app.stop()


async def test_agent_close_failure_is_reported_and_still_finalizes_extensions(tmp_path):
    calls, retained, finalized = [], [], []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='completed model work'),)))
    app = application(tmp_path, stream, tools=())
    try:
        session = await prepared(app)
        runtime = session.children.runtime
        original_factory, original_finalize = runtime.factory, runtime.finalize
        async def factory(spec, publish):
            agent = await original_factory(spec, publish)
            retained.append(agent)
            original_close = agent.aclose
            async def close():
                await original_close()
                raise OSError('actual Agent close reported cleanup failure')
            agent.aclose = close
            return agent
        async def finalize(spec):
            assert retained[0].closed
            finalized.append(spec.id)
            if original_finalize is not None:
                await original_finalize(spec)
        runtime.factory, runtime.finalize = factory, finalize
        identifier = await runtime.start('close failure must not publish completed', config={'tools': []})
        record = await runtime.control('wait', identifier)
        assert record['status'] == 'failed' and record['error'] == 'Child resource cleanup failed: OSError'
        assert finalized == [identifier] and retained[0].closed
        assert session.children.store.load()[0]['error'] == record['error']
        before = len(calls)
        with pytest.raises(RuntimeError, match='closing or closed'):
            await retained[0].prompt('no model replay after failed host cleanup')
        assert len(calls) == before == 1
    finally:
        await app.stop()
