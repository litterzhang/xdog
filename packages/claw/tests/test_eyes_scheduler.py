"""Eyes clocks/checkpoints, durable managed admission and group-scoped tools."""
from __future__ import annotations

import sys

import pytest
from xdog.eyes.sources.scheduler import SchedulerSource

from packages.claw.tests.eyes_helpers import NOW, at, scheduler
from packages.claw.tests.eyes_helpers import managed as managed


def source(harness, **schedule):
    definition = harness.manager.definition('g1', 'source-unit', scheduler(**schedule), 'report')
    return SchedulerSource(definition.sources[0], wall_now=harness.clock.wall(),
                           monotonic_now=harness.clock.monotonic())


def poll(timer, harness, **kwargs):
    return timer.poll(wall_now=harness.clock.wall(), monotonic_now=harness.clock.monotonic(), **kwargs)


async def test_interval_first_run_waits_and_repeats(managed):
    timer = source(managed, every_seconds=60)
    assert not poll(timer, managed).occurrences
    managed.clock.advance(60)
    assert len(poll(timer, managed).occurrences) == 1
    assert not poll(timer, managed).occurrences
    managed.clock.advance(59)
    assert not poll(timer, managed).occurrences
    managed.clock.advance(1)
    assert len(poll(timer, managed).occurrences) == 1


async def test_one_off_and_reload(managed):
    timer = source(managed, **at(60)['schedule'])
    assert not poll(timer, managed).occurrences
    managed.clock.advance(60)
    assert len(poll(timer, managed).occurrences) == 1 and timer.completed
    definition = timer.definition
    checkpoint = timer.export_checkpoint(wall_now=managed.clock.wall(), monotonic_now=managed.clock.monotonic())
    managed.clock.advance(86400)
    restored = SchedulerSource.restore(definition, checkpoint, wall_now=managed.clock.wall(),
                                       monotonic_now=managed.clock.monotonic())
    assert restored.completed and not poll(restored, managed).occurrences


@pytest.mark.parametrize('zone,delta', [('Asia/Shanghai', 240), ('UTC', 8 * 3600 + 240)])
async def test_cron_first_time_and_timezone(managed, zone, delta):
    timer = source(managed, cron='24 9 12 10 *', timezone=zone)
    managed.clock.advance(delta - 1)
    assert not poll(timer, managed).occurrences
    managed.clock.advance(1)
    occurrence, = poll(timer, managed).occurrences
    assert occurrence.scheduled_at == NOW + delta and occurrence.timezone == zone
    managed.clock.advance(300 * 86400)
    assert not poll(timer, managed).occurrences  # Annual cron is not a one-off or a fallback interval.


@pytest.mark.parametrize('schedule', [
    {}, {'cron': '* * * * *'}, {'cron': '* * * * *', 'timezone': 'not/a-zone'},
    {'cron': 'bad', 'timezone': 'UTC'}, {'cron': '* * * * * *', 'timezone': 'UTC'},
    {'cron': '99 9 * * *', 'timezone': 'UTC'}, {'every_seconds': 0}, {'every_seconds': -1},
    {'at': float('nan')}, {'at': at()['schedule']['at'], 'every_seconds': 2},
])
async def test_invalid_schedule_not_persisted(managed, schedule):
    with pytest.raises(ValueError):
        await managed.manager.create('g1', 'invalid', scheduler(**schedule), 'report')
    assert await managed.manager.list('g1') == ()
    assert not managed.manager._tasks and not managed.manager._handlers and not managed.model.calls


async def test_missing_croniter_fails_closed(managed, monkeypatch):
    monkeypatch.setitem(sys.modules, 'croniter', None)
    with pytest.raises((ValueError, ImportError), match='croniter'):
        await managed.create(source=scheduler(cron='* * * * *', timezone='UTC'))
    assert await managed.manager.list('g1') == () and not managed.model.calls


async def test_tool_immediate_visibility_scope_and_cancel(managed):
    text = await managed.tool('create', name='check', source=at(), instruction='check')
    created, = await managed.manager.list('g1')
    assert created['id'] in text and created['enabled']
    assert created['id'] in await managed.tool('status', observer_id=created['id'])
    assert not managed.model.calls and await managed.manager.list('g2') == ()
    assert created['id'] not in await managed.tool('list', group='g2')
    for action in ('status', 'cancel'):
        with pytest.raises(ValueError, match='Unknown observer'):
            await managed.tool(action, group='g2', observer_id=created['id'])
    assert (await managed.manager.status('g1', created['id']))['enabled']
    text = await managed.tool('cancel', observer_id=created['id'])
    assert 'cancel' in text.lower()
    managed.clock.advance(240)
    await managed.flush()
    assert not managed.model.calls
    await managed.restart()
    assert not (await managed.manager.status('g1', created['id']))['enabled']


@pytest.mark.parametrize('args', [
    {}, {'source': scheduler(at='2026-10-12T09:24:00')},
    {'source': scheduler(at='2026-10-12T09:19:00+08:00')}, {'source': scheduler(at='invalid')},
    {'source': scheduler(cron='* * * * *')},
    {'source': {**scheduler(every_seconds=60), 'group_id': 'g2'}},
    {'source': scheduler(every_seconds=60), 'instruction': ' '},
    {'source': scheduler(at='2026-10-12T09:24:00+08:00', cron='* * * * *', timezone='UTC')},
])
async def test_tool_errors_are_explicit(managed, args):
    with pytest.raises(ValueError):
        await managed.tool('create', **{'name': 'invalid', 'instruction': 'report', **args})
    assert await managed.manager.list('g1') == ()


async def test_tool_requires_live_service_and_owner(managed):
    tool = managed.manager.tools[0]
    for ctx in ({}, {'group_id': 'unknown', 'data_dir': '/must/not/write'}):
        with pytest.raises(ValueError, match='owner'):
            await tool.execute('x', {'action': 'create', 'name': 'check',
                                     'instruction': 'check', 'source': scheduler(every_seconds=60)}, ctx=ctx)
        assert await managed.manager.list('g1') == ()


async def test_failed_save_does_not_publish_task(managed):
    await managed.manager.store._disk(lambda db: db.execute(
        "CREATE TRIGGER disk_full BEFORE INSERT ON observers "
        "BEGIN SELECT RAISE(ABORT, 'disk full'); END"))
    with pytest.raises(Exception, match='disk full'):
        await managed.create()
    assert await managed.manager.list('g1') == ()
    assert not managed.manager._tasks and not managed.manager._handlers and not managed.model.calls


async def test_missing_dependency_on_reload_disables_and_persists_error(managed, monkeypatch):
    created = await managed.create(source=scheduler(cron='* * * * *', timezone='UTC'))
    await managed.replace_manager()
    monkeypatch.setitem(sys.modules, 'croniter', None)
    await managed.manager.start()
    saved = await managed.manager.status('g1', created['id'])
    assert not saved['enabled'] and 'croniter' in saved['error'] and saved['state'] == 'needs_review'
    await managed.replace_manager()
    await managed.manager.start()
    assert await managed.manager.status('g1', created['id']) == saved
    assert not managed.model.calls


async def test_late_recurring_tick_coalesces_occurrences(managed):
    timer = source(managed, cron='* * * * *', timezone='UTC')
    managed.clock.advance(600)
    assert len(poll(timer, managed).occurrences) == 1
    assert not poll(timer, managed).occurrences
    managed.clock.advance(75)
    assert not poll(timer, managed, suspended=True).occurrences
    assert not poll(timer, managed).occurrences
    managed.clock.advance(44)
    assert not poll(timer, managed).occurrences
    managed.clock.advance(1)
    assert len(poll(timer, managed).occurrences) == 1


async def test_actual_agent_loop_marks_descriptor_errors_and_rejects_host_override_arguments(managed, monkeypatch):
    from unittest.mock import Mock

    from xdog.ai.types import AssistantMessage, ToolCall, ToolResultMessage
    from xdog.claw.core.types import UserInput

    host = managed.manager.host
    scope = Mock(wraps=host.tool_scope)
    reply = Mock(wraps=host.capture_reply)
    monkeypatch.setattr(host, 'tool_scope', scope)
    monkeypatch.setattr(host, 'capture_reply', reply)
    cases = [
        {'owner': 'g2'}, {'group_id': 'g2'}, {'reply': None}, {'reply_reference': None},
        {'notify_only': None}, {'service': None}, {'_eyes': None},
    ]
    for index, extra in enumerate(cases):
        params = {'action': 'create', 'name': 'must-not-exist', 'source': at(), 'instruction': 'report', **extra}
        # Also cover descriptor callers that do not run Agent's schema validator.
        with pytest.raises(ValueError, match='exact required parameters'):
            await managed.manager.tools[0].execute('direct', params, ctx={'group_id': 'g1'})
        prompt = f'forged host binding {index}'
        managed.model.replies[prompt] = AssistantMessage(content=(ToolCall(
            id=f'forged-{index}', name='eyes', arguments=params),), stop_reason='toolUse')
        await managed.orch.route_message(UserInput(group_id='g1', content=prompt),
                                         reply_reference=managed.reference('g1'))
        messages = managed.orch._runtimes['g1'].get_or_create_session().agent.state.messages
        result = next(item for item in messages if isinstance(item, ToolResultMessage)
                      and item.tool_call_id == f'forged-{index}')
        assert result.is_error is True
    scope.assert_not_called()
    reply.assert_not_called()

    # Valid outer schema reaches the real descriptor and raises in source validation.
    # It must still produce a standard error ToolResultMessage, not successful text.
    managed.model.replies['invalid source'] = AssistantMessage(content=(ToolCall(
        id='invalid-source', name='eyes', arguments={'action': 'create', 'name': 'invalid-source',
        'source': scheduler(every_seconds=0), 'instruction': 'report'}),), stop_reason='toolUse')
    await managed.orch.route_message(UserInput(group_id='g1', content='invalid source'),
                                     reply_reference=managed.reference('g1'))
    messages = managed.orch._runtimes['g1'].get_or_create_session().agent.state.messages
    result = next(item for item in messages if isinstance(item, ToolResultMessage)
                  and item.tool_call_id == 'invalid-source')
    assert result.is_error is True and scope.called and reply.called
    assert await managed.manager.list('g1') == () and await managed.manager.list('g2') == ()
    assert not managed.manager._tasks and not managed.manager._files
