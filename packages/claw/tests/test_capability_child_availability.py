"""Child lifecycle hints recheck real group facts on the original capability owner."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from xdog.agent.session_input import SessionInput
from xdog.claw.core.inputs.models import InputScope

from packages.claw.tests.test_capability_execution import app_owner, no_business_providers  # noqa: F401


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'canceled'])
async def test_main_settles_before_child_and_child_settlement_rechecks_availability(tmp_path, monkeypatch, outcome):
    app, runtime, host = await app_owner(tmp_path)
    release, entered = asyncio.Event(), asyncio.Event()
    committed, release_commit = threading.Event(), threading.Event()
    events, threads = [], []
    try:
        await host.prepare_parent()
        session = runtime._active_session
        children = session.children.runtime
        original_factory, original_persist = children.factory, children.persist

        async def factory(spec, publish):
            entered.set()
            await release.wait()
            if outcome == 'failed':
                raise RuntimeError('actual child preparation failure')
            return await original_factory(spec, publish)

        def persist(record):
            committed.set()
            assert release_commit.wait(5)
            original_persist(record)

        monkeypatch.setattr(children, 'factory', factory)
        monkeypatch.setattr(children, 'persist', persist)
        # A replaced/failed UI sink cannot remove the independent lifecycle hint.
        def disconnected(_):
            raise RuntimeError('UI disconnected')
        children.publish = disconnected
        owner_thread = threading.get_ident()
        def observe(event):
            threads.append(threading.get_ident())
            events.append(event)
        unsubscribe = host.subscribe_events(observe)
        child = await children.start('independent child', config={'tools': [], 'skills': []})
        await asyncio.wait_for(entered.wait(), 5)
        scope = InputScope(host.identity, host.workspace)
        await app.accept_external(scope, SessionInput('main-turn', 'automatic', 'test', 'main work', (), {}))
        inputs = await app.input_service()
        result = await inputs.wait(scope, 'main-turn')
        assert result.status == 'succeeded'
        settled, = [event for event in events if event['type'] == 'execution.settled']
        assert settled['snapshot']['state'] == 'idle' and settled['snapshot']['children_active']
        events.clear()
        if outcome == 'canceled':
            finishing = asyncio.create_task(children.control('cancel', child))
        else:
            release.set()
            finishing = asyncio.create_task(children.control('wait', child))
        assert await asyncio.to_thread(committed.wait, 5)
        # Even an apparently terminal hint cannot bypass actual persistence/cleanup.
        listener, = children._listeners.values()
        await asyncio.to_thread(listener, {'type': 'child_status', 'snapshot': {'children_active': False}})
        assert host.execution_snapshot()['children_active']
        assert not [event for event in events if event['type'] == 'owner.available']
        release_commit.set()
        assert (await asyncio.wait_for(finishing, 5))['status'] == outcome
        await asyncio.sleep(0)
        available = [event for event in events if event['type'] == 'owner.available']
        assert available and all(event['snapshot']['state'] == 'idle'
                                 and not event['snapshot']['children_active'] for event in available)
        assert all(thread == owner_thread for thread in threads)
        assert not [event for event in events if event['type'] == 'execution.settled']
        assert (await host.latest_execution())['execution_id'] == result.attempt_id
        unsubscribe()
    finally:
        release.set()
        release_commit.set()
        await app.stop()


@pytest.mark.parametrize('fence', ['retire', 'closing', 'workspace', 'generation', 'session', 'children', 'agent'])
async def test_queued_child_hints_cannot_escape_original_owner(tmp_path, monkeypatch, fence):
    app, runtime, host = await app_owner(tmp_path)
    try:
        await host.prepare_parent()
        children = runtime._active_session.children.runtime
        listener, = children._listeners.values()
        events = []
        unsubscribe = host.subscribe_events(events.append)
        events.clear()
        # Queue on the original loop, then invalidate before the callback runs.
        listener({'type': 'child_status'})
        if fence == 'retire':
            host.retire()
            assert not children._listeners
        elif fence == 'closing':
            monkeypatch.setattr(runtime, '_closing', True)
        elif fence == 'workspace':
            monkeypatch.setattr(runtime, 'workspace_dir', tmp_path / 'other')
        elif fence == 'generation':
            monkeypatch.setattr(app.group_controller(host.identity), 'generation', 'successor')
        elif fence == 'session':
            runtime.reset_session()
            await host.prepare_parent()
            assert not children._listeners
        elif fence == 'children':
            monkeypatch.setattr(runtime._active_session.children, 'runtime', SimpleNamespace(active=False))
        else:
            monkeypatch.setattr(runtime._active_session, '_agent', SimpleNamespace(is_idle=True))
        await asyncio.sleep(0)
        assert not [event for event in events if event['type'] == 'owner.available']
        # Even a captured callback invoked after unsubscription remains fenced.
        await asyncio.to_thread(listener, {})
        await asyncio.sleep(0)
        assert not [event for event in events if event['type'] == 'owner.available']
        unsubscribe()
    finally:
        monkeypatch.undo()
        await app.stop()


async def test_child_hints_require_original_loop_but_ignore_payload(tmp_path):
    app, runtime, host = await app_owner(tmp_path)
    try:
        await host.prepare_parent()
        listener, = runtime._active_session.children.runtime._listeners.values()
        events = []
        unsubscribe = host.subscribe_events(events.append)
        events.clear()
        # Reattaching the same parent must preserve both the subscription and
        # a queued real availability hint, rather than invalidating it.
        listener({'snapshot': {'state': 'closing', 'children_active': True}})
        host.parent(runtime._active_session)
        assert tuple(runtime._active_session.children.runtime._listeners.values()) == (listener,)
        await asyncio.sleep(0)
        available, = events
        assert available['type'] == 'owner.available'
        assert available['snapshot']['state'] == 'idle' and not available['snapshot']['children_active']
        events.clear()
        await asyncio.to_thread(listener, {})
        await asyncio.sleep(0)
        assert len(events) == 1 and events[0]['type'] == 'owner.available'

        def wrong_loop():
            async def read():
                host.execution_snapshot()
            asyncio.run(read())
        with pytest.raises(RuntimeError, match='original owner loop'):
            await asyncio.to_thread(wrong_loop)
        unsubscribe()
    finally:
        await app.stop()


async def test_one_child_settling_does_not_make_other_live_children_available(tmp_path, monkeypatch):
    app, runtime, host = await app_owner(tmp_path)
    gates = {name: asyncio.Event() for name in ('first', 'second')}
    try:
        await host.prepare_parent()
        children = runtime._active_session.children.runtime
        original = children.factory
        async def factory(spec, publish):
            await gates[spec.task].wait()
            return await original(spec, publish)
        monkeypatch.setattr(children, 'factory', factory)
        first = await children.start('first', config={'tools': [], 'skills': []})
        second = await children.start('second', config={'tools': [], 'skills': []})
        events = []
        unsubscribe = host.subscribe_events(events.append)
        events.clear()
        gates['first'].set()
        assert (await children.control('wait', first))['status'] == 'completed'
        await asyncio.sleep(0)
        assert host.execution_snapshot()['children_active'] and not events
        gates['second'].set()
        assert (await children.control('wait', second))['status'] == 'completed'
        await asyncio.sleep(0)
        assert events and all(event['type'] == 'owner.available'
                              and not event['snapshot']['children_active'] for event in events)
        unsubscribe()
    finally:
        for gate in gates.values():
            gate.set()
        await app.stop()
