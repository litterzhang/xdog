"""Eyes tool -> managed source/outbox -> Unix ACP -> actual Agent queue -> channel."""
from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import AsyncMock, patch

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.claw.core.memory.manager import MemoryManager
from xdog.claw.core.types import UserInput

from packages.claw.tests.eyes_helpers import at, scheduler
from packages.claw.tests.eyes_helpers import managed as managed


async def create(harness, instruction='report', group='g1', source=None):
    harness.serial += 1
    text = await harness.tool('create', group=group, name=f'tool-observer-{harness.serial}',
                              source=source or at(), instruction=instruction)
    assert not text.startswith('Error:'), text
    return json.loads(text)


async def test_future_tool_task_runs_once_reports_and_reloads(managed):
    created = await create(managed)
    assert not (managed.orch._data_dir / 'scheduled_tasks.json').exists()
    await managed.flush()
    assert not managed.model.calls and managed.channel.send_message.await_count == 0
    managed.clock.advance(239)
    await managed.flush()
    assert not managed.model.calls
    managed.clock.advance(1)
    await managed.orch.tick()
    await managed.orch.tick()  # Goal ticks neither admit nor duplicate Eyes source work.
    saved = await managed.completed(created['id'])
    managed.channel.send_message.assert_awaited_once_with('g1', 'Scheduled report')
    assert managed.model.calls == ['report']
    occurrence, = saved['occurrences']
    assert occurrence['receipt_status'] == 'accepted' and occurrence['started'] == 1
    assert occurrence['execution_status'] == 'succeeded' and occurrence['delivery_status'] == 'delivered'
    assert not saved['enabled'] and saved['state'] == 'completed'
    await managed.restart()
    assert await managed.manager.status('g1', created['id']) == saved
    managed.clock.advance(365 * 86400)
    await managed.flush()
    assert managed.model.calls == ['report'] and managed.channel.send_message.await_count == 1


async def test_model_can_create_task_via_real_tool_loop(managed):
    managed.model.replies['schedule now'] = AssistantMessage(content=(ToolCall(
        id='schedule-1', name='eyes', arguments={'action': 'create', 'name': 'follow-up',
                                                'source': at(), 'instruction': 'scheduled follow-up'}),),
        stop_reason='toolUse')
    result = await managed.orch.route_message(UserInput(group_id='g1', content='schedule now'))
    assert result and not result.error
    created, = await managed.manager.list('g1')
    record = await managed.record(created['id'])
    assert record.definition['handlers'][0]['instruction'] == 'scheduled follow-up'
    session = managed.orch._runtimes['g1'].get_or_create_session()
    assert session.agent.tool_context['_eyes'] is managed.manager
    assert session.agent.tool_context['_memory_search'] is not None or managed.orch._runtimes['g1'].memory is None
    managed.channel.send_message.assert_not_awaited()
    managed.clock.advance(240)
    await managed.completed(created['id'])
    assert managed.model.calls[-1] == 'scheduled follow-up'
    assert managed.orch._runtimes['g1'].get_or_create_session() is session
    managed.channel.send_message.assert_awaited_once_with('g1', 'Scheduled report')


@pytest.mark.parametrize('cancel', [False, True])
async def test_busy_group_waits_without_steering_or_losing_callback(managed, cancel):
    entered, release = asyncio.Event(), asyncio.Event()
    managed.model.gates['busy'] = entered, release
    user = asyncio.create_task(managed.orch.route_message(UserInput(group_id='g1', content='busy')))
    managed.users.append(user)
    await asyncio.wait_for(entered.wait(), 3)
    runtime = managed.orch._runtimes['g1']
    with patch.object(runtime, 'steer', wraps=runtime.steer) as steer, patch.object(
        runtime, 'follow_up', wraps=runtime.follow_up,
    ) as follow_up:
        created = await create(managed)
        managed.clock.advance(240)

        async def admitted():
            status = await managed.manager.status('g1', created['id'])
            rows = status['occurrences']
            return rows and rows[0]['receipt_status'] == 'accepted' and rows[0]['execution_status'] == 'running'
        await managed.until(admitted)
        rows = (await managed.manager.status('g1', created['id']))['occurrences']
        assert rows[0]['started'] == 0 and managed.model.calls == ['busy']
        steer.assert_not_called()
        follow_up.assert_not_called()
        if cancel:
            outcome = json.loads(await managed.tool('cancel', observer_id=created['id']))
            assert outcome['not_started'] == 1 and outcome['already_started'] == 0
            row, = (await managed.manager.status('g1', created['id']))['occurrences']
            assert row['cancelled'] == 1 and row['execution_status'] == 'cancelled'
            assert row['delivery_status'] == 'not-required'
        release.set()
        result = await asyncio.wait_for(user, 3)
        assert result and not result.error
        if cancel:
            await managed.flush()
            assert managed.model.calls == ['busy']
            managed.channel.send_message.assert_not_awaited()
            await managed.restart()
            assert not (await managed.manager.status('g1', created['id']))['enabled']
        else:
            await managed.completed(created['id'])
            assert managed.model.calls == ['busy', 'report']
            managed.channel.send_message.assert_awaited_once_with('g1', 'Scheduled report')
        steer.assert_not_called()
        follow_up.assert_not_called()


async def test_slow_task_does_not_block_other_group_or_next_tick(managed):
    entered, release = asyncio.Event(), asyncio.Event()
    managed.model.gates['slow'] = entered, release
    slow = await create(managed, instruction='slow')
    managed.clock.advance(240)
    await asyncio.wait_for(managed.orch.tick(), 1)
    await asyncio.wait_for(entered.wait(), 3)
    fast = await create(managed, group='g2', instruction='fast', source=scheduler(every_seconds=1))
    managed.clock.advance(1)
    await asyncio.wait_for(managed.orch.tick(), 1)
    await managed.completed(fast['id'], group='g2')
    assert 'fast' in managed.model.calls and not release.is_set()
    assert managed.model.peak == 2
    managed.channel.send_message.assert_awaited_with('g2', 'Scheduled report')
    release.set()
    await managed.completed(slow['id'])


async def test_global_concurrency_bound_preserves_independent_group_owners(managed):
    created = []
    for group in ('g1', 'g2', 'g3'):
        managed.model.gates[group] = asyncio.Event(), asyncio.Event()
        created.append((group, await create(managed, instruction=group, group=group)))
    managed.clock.advance(240)

    async def at_capacity():
        return managed.model.active == 2
    await managed.until(at_capacity)
    assert managed.model.peak == 2
    assert sum(entered.is_set() for entered, _ in managed.model.gates.values()) == 2
    for entered, release in managed.model.gates.values():
        if entered.is_set():
            release.set()

    async def third_started():
        return all(entered.is_set() for entered, _ in managed.model.gates.values())
    await managed.until(third_started)
    for _, release in managed.model.gates.values():
        release.set()
    for group, row in created:
        await managed.completed(row['id'], group=group)
    assert managed.model.peak == 2 and sorted(managed.model.calls) == ['g1', 'g2', 'g3']
    assert managed.channel.send_message.await_count == 3


async def test_recurring_failure_advances_instead_of_retrying_same_occurrence(managed):
    created = await create(managed, source=scheduler(every_seconds=60))
    managed.clock.advance(60)
    managed.channel.send_message.side_effect = RuntimeError('offline')
    first = await managed.completed(created['id'])
    row, = first['occurrences']
    assert row['execution_status'] == 'succeeded' and row['delivery_status'] == 'failed'
    assert 'offline' in row['delivery_error'] and first['enabled']
    first_identity = row['message_id']
    await managed.flush()
    assert managed.model.calls == ['report']
    managed.channel.send_message.side_effect = None
    managed.clock.advance(60)
    second = await managed.completed(created['id'], count=2)
    row = next(row for row in second['occurrences'] if row['message_id'] != first_identity)
    assert row['execution_status'] == 'succeeded' and row['delivery_status'] == 'delivered'
    assert managed.model.calls == ['report', 'report'] and second['enabled']
    assert len({row['message_id'] for row in second['occurrences']}) == 2


async def test_shutdown_settles_active_scheduled_run_without_success(managed):
    entered, release = asyncio.Event(), asyncio.Event()
    managed.model.gates['report'] = entered, release
    created = await create(managed)
    managed.clock.advance(240)
    await asyncio.wait_for(entered.wait(), 3)
    await asyncio.wait_for(managed.manager.close(), 3)
    await asyncio.wait_for(managed.server.close(), 3)
    assert not managed.manager._tasks
    assert all(task.done() for task in managed.server.backend._workers.values())
    # Receiver restart fences an admitted model turn; it cannot become fresh work.
    await managed.restart()
    saved = await managed.manager.status('g1', created['id'])
    row, = saved['occurrences']
    assert row['execution_status'] == 'interrupted' and not saved['enabled']
    assert saved['state'] == 'needs_review' and saved['error']
    assert row['delivery_status'] != 'delivered'
    managed.channel.send_message.assert_not_awaited()
    managed.clock.advance(86400)
    await managed.flush()
    assert managed.model.calls == ['report']
    release.set()


@pytest.mark.parametrize('backend_failure', [False, True])
async def test_scheduled_memory_search_refreshes_and_reports(managed, monkeypatch, backend_failure):
    monkeypatch.setattr('xdog.claw.core.memory.indexer._embedding_model_instance', None)
    for name in ('sentence_transformers', 'sqlite_vec', 'rank_bm25'):
        monkeypatch.setitem(sys.modules, name, None)
    runtime = managed.orch._runtimes['g1']
    runtime.workspace_dir.mkdir(parents=True, exist_ok=True)
    memory = MemoryManager(runtime.workspace_dir, runtime.data_dir, 'g1')
    runtime.memory = memory
    runtime._enabled_tools = ('eyes', 'memory')
    if backend_failure:
        memory._searcher.search = AsyncMock(side_effect=RuntimeError('backend unavailable'))
    managed.model.replies['memory report'] = AssistantMessage(content=(ToolCall(
        id='memory-search', name='memory', arguments={'action': 'search', 'query': '陆知行 xdog-youju'}),),
        stop_reason='toolUse')
    created = await create(managed, instruction='memory report', source=scheduler(every_seconds=60))
    session = runtime.get_or_create_session()
    ctx = session.agent.tool_context
    assert ctx['_memory_search'].__self__ is memory and ctx['_eyes'] is managed.manager
    for count, revision in enumerate(('initialneedle', 'updatedneedle'), 1):
        text = f'陆知行 xdog-youju {revision}'
        (runtime.workspace_dir / 'MEMORY.md').write_text(text, encoding='utf-8')
        managed.model.replies[f'[MEMORY.md] {text}'] = AssistantMessage(content=(TextContent(text=text),))
        managed.clock.advance(60)
        saved = await managed.completed(created['id'], count=count)
        assert managed.model.calls[-2:] == ['memory report', f'[MEMORY.md] {text}']
        managed.channel.send_message.assert_awaited_with('g1', text)
        assert all(row['execution_status'] == 'succeeded' and row['delivery_status'] == 'delivered'
                   for row in saved['occurrences'])
        assert saved['enabled'] and runtime.get_or_create_session() is session
        assert session.agent.tool_context['_eyes'] is managed.manager
        assert session.agent.tool_context['_memory_search'].__self__ is memory
    assert managed.channel.send_message.await_count == 2
    await managed.restart()
    assert (await managed.manager.status('g1', created['id']))['occurrences'] == saved['occurrences']


async def test_slow_recurring_run_skips_missed_deadlines_but_retains_fixed_phase(managed):
    entered, release = asyncio.Event(), asyncio.Event()
    managed.model.gates['report'] = entered, release
    created = await create(managed, source=scheduler(every_seconds=60))
    managed.clock.advance(60)
    await asyncio.wait_for(entered.wait(), 3)
    session = managed.orch._runtimes['g1'].get_or_create_session()
    managed.clock.advance(75)  # Cross phase 120 while the original model owns the turn.
    await managed.flush()
    assert managed.model.calls == ['report'] and managed.model.peak == 1
    release.set()
    first = await managed.completed(created['id'])
    first_identity = first['occurrences'][0]['message_id']
    managed.clock.advance(44)
    await managed.flush()
    assert managed.model.calls == ['report']
    managed.clock.advance(1)  # Next phase is 180, not completion(135)+60=195.
    second = await managed.completed(created['id'], count=2)
    assert managed.model.calls == ['report', 'report'] and managed.model.peak == 1
    assert managed.orch._runtimes['g1'].get_or_create_session() is session
    assert any(row['message_id'] != first_identity for row in second['occurrences'])
    assert all(row['delivery_status'] == 'delivered' for row in second['occurrences'])


@pytest.mark.parametrize('pending_only', [False, True])
async def test_goal_tick_retains_current_group_tools_and_eyes_context(managed, monkeypatch, pending_only):
    first = managed.orch._runtimes['g1']
    second = managed.orch._runtimes['g2']
    for runtime in (first, second):
        monkeypatch.setattr(runtime.goal_manager, 'has_active_goals', lambda: not pending_only)
        monkeypatch.setattr(runtime.goal_manager, 'has_pending_work', lambda: pending_only)
    failed_tick = AsyncMock(side_effect=RuntimeError('goal verification failed'))
    next_tick = AsyncMock()
    monkeypatch.setattr(first.goal_manager, 'tick', failed_tick)
    monkeypatch.setattr(second.goal_manager, 'tick', next_tick)
    # Failure in one goal group must not stop the next group's goal tick.
    await managed.orch.tick()
    for runtime, tick in ((first, failed_tick), (second, next_tick)):
        tick.assert_awaited_once()
        assert tick.call_args.kwargs['tools'] == runtime.tools
        assert tick.call_args.kwargs['tool_ctx'] == {
            'group_id': runtime.group.id,
            'workspace_dir': str(runtime.workspace_dir),
            'data_dir': str(runtime.data_dir),
            '_eyes': managed.manager,
        }
    assert not managed.model.calls and await managed.manager.list('g1') == ()
    managed.channel.send_message.assert_not_awaited()


async def test_goal_directive_remains_system_input_without_managed_report(managed, monkeypatch):
    from xdog.claw.core.types import SystemInput, SystemInputKind

    runtime = managed.orch._runtimes['g1']
    route = AsyncMock(wraps=managed.orch.route_message)
    monkeypatch.setattr(runtime.goal_manager, '_route_fn', route)
    goal = runtime.goal_manager.create('g1', 'Persistent goal', 'Continue until evidence is checked',
                                       ['Inspect the actual result'], [])
    runtime.goal_manager._put(goal, phase='waiting', pending_notice='continue goal')
    await runtime.goal_manager.tick()
    route.assert_awaited_once()
    message = route.call_args.args[0]
    assert isinstance(message, SystemInput) and message.kind is SystemInputKind.GOAL_RUNNER
    assert message.group_id == 'g1' and message.content == 'continue goal'
    assert managed.model.calls == ['continue goal']
    assert await managed.manager.list('g1') == ()
    managed.channel.send_message.assert_not_awaited()
