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
        text = await managed.tool(action, group='g2', observer_id=created['id'])
        assert text.startswith('Error:')
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
    text = await managed.tool('create', **{'name': 'invalid', 'instruction': 'report', **args})
    assert text.startswith('Error:')
    assert await managed.manager.list('g1') == ()


async def test_tool_requires_live_service_and_owner():
    from xdog.claw.core.tools.tool_eyes import create_eyes_tool
    tool = create_eyes_tool()
    for ctx in ({}, {'group_id': 'g1', 'data_dir': '/must/not/write'}):
        result = await tool.execute('x', {'action': 'create', 'name': 'check',
                                         'instruction': 'check', 'source': scheduler(every_seconds=60)}, ctx=ctx)
        assert result.content[0].text.startswith('Error:')
        assert 'running' in result.content[0].text.lower() or 'live' in result.content[0].text.lower()


async def test_failed_save_does_not_publish_task(managed):
    await managed.server.backend.inbox._disk(lambda db: db.execute(
        "CREATE TRIGGER disk_full BEFORE INSERT ON managed_observers "
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
