"""Settled Coding children permanently retire their retained Agent handles."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.ai.types import AssistantMessage, ImageContent, TextContent
from xdog.ai.utils.event_stream import EventStream

from packages.coding.tests.extension_session_helpers import PNG
from packages.coding.tests.test_child_agents import create
from packages.coding.tests.test_child_host_boundary import close


@pytest.mark.parametrize('end', ['completed', 'cancel', 'owner_close', 'close_failure'])
async def test_settlement_drains_inputs_before_permanent_agent_close_and_never_reopens_model(tmp_path, end):
    session, _ = create(tmp_path)
    retained, calls, finalized = [], [], []
    entered = threading.Event()
    def stream(model, context, options):
        calls.append(context)
        if end == 'completed':
            return EventStream.empty(AssistantMessage(content=(TextContent(text='done'),)))
        async def held():
            entered.set()
            await asyncio.Event().wait()
            yield  # Cancelled while held; the provider never completes another response.
        return EventStream.from_async_generator(held())
    session.agent.set_stream_fn(stream)
    actual_factory = session.children.create
    async def factory(spec, publish):
        agent = await actual_factory(spec, publish)
        retained.append(agent)
        if end == 'close_failure':
            async def fail_after_drain():
                assert not agent.pending_inputs()
                raise OSError('Injected owned Agent shutdown failure')
            agent._close_owned_runtime = fail_after_drain
        return agent
    owner = session.children.runtime
    owner.factory = factory
    actual_finalize = owner.finalize
    async def finalize(spec):
        agent = retained[0]
        finalized.append((agent.closed, agent.is_streaming, tuple(agent.pending_inputs())))
        await actual_finalize(spec)
    owner.finalize = finalize
    try:
        identifier = await owner.start('first task')
        if end != 'completed':
            assert await asyncio.to_thread(entered.wait, 3)
            await owner.control('follow-up', identifier, 'UNDISPATCHED MATERIALS',
                images=(ImageContent(data=PNG, mime_type='image/png'),))
        if end == 'owner_close':
            await owner.aclose()
            record = session.children.store.load()[0]
        else:
            record = await owner.control('wait' if end == 'completed' else 'cancel', identifier)
        child = retained[0]
        assert not child.is_streaming and not child.is_idle and not child.pending_inputs() and len(calls) == 1
        assert record['status'] == ('completed' if end == 'completed' else
                                    'failed' if end == 'close_failure' else 'canceled')
        if end == 'close_failure':
            assert 'Child resource cleanup failed: OSError' in record['error']
            assert finalized == [(False, False, ())]
        else:
            assert child.closed and finalized == [(True, False, ())]
        if end != 'completed':
            pending = record['undelivered_inputs']
            assert len(pending) == 1 and pending[0]['content'] == 'UNDISPATCHED MATERIALS'
            assert pending[0]['images'][0]['data'] == PNG
            saved = session.children.store.load()[0]
            assert saved['undelivered_inputs'] == pending
            assert 'UNDISPATCHED MATERIALS' not in str(saved['messages'])
        with pytest.raises(RuntimeError, match='closing or closed'):
            await child.prompt('retained handle must never start a second model')
        assert len(calls) == 1 and not session.children.permissions and not session.children.extensions.owners
        assert owner.capture_source(identifier).settling is False
    finally:
        await close(session)
