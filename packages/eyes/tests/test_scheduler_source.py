"""Scheduler definitions and occurrences are independent of file snapshots and agents."""
from datetime import datetime

import pytest
from xdog.eyes.errors import ObserverValidationError
from xdog.eyes.loader import definition_data, parse


def definition(data, schedule):
    data['sources'] = [{'id': 'timer', 'type': 'scheduler', 'schedule': schedule}]
    data['detectors'] = [{'id': 'due', 'type': 'scheduler.due', 'source': 'timer'}]
    data['routes'][0].update(detector='due', event_types=['scheduler.due'])
    return data


def timestamp(text):
    return datetime.fromisoformat(text).timestamp()


@pytest.mark.parametrize('schedule', [
    {'at': '2030-01-01T09:00:00+08:00'},
    {'every_seconds': 300},
    {'cron': '0 9 * * *', 'timezone': 'Asia/Shanghai'},
])
def test_scheduler_definitions_round_trip_without_file_fields(tmp_path, observer_data, schedule):
    result = parse(definition(observer_data, schedule), base_dir=tmp_path)
    assert result.sources[0].type == 'scheduler' and not hasattr(result.sources[0], 'root')
    assert parse(definition_data(result), base_dir=tmp_path) == result
    assert result.routes[0].event_types == ('scheduler.due',)


@pytest.mark.parametrize('schedule', [
    {}, {'at': '2030-01-01'}, {'at': 'not-a-date'}, {'at': '2030-01-01T00:00:00'},
    {'at': '2030-01-01T00:00:00Z', 'every_seconds': 1},
    {'every_seconds': True}, {'every_seconds': 0}, {'every_seconds': -1},
    {'every_seconds': .00001}, {'every_seconds': float('nan')}, {'every_seconds': float('inf')},
    {'cron': '* * * * *'}, {'cron': '* * * * * *', 'timezone': 'UTC'},
    {'cron': '0 25 * * *', 'timezone': 'UTC'}, {'cron': '0 9 * * *', 'timezone': 'not/a-zone'},
    {'cron': '0 0 31 2 *', 'timezone': 'UTC'}, {'cron': 'R * * * *', 'timezone': 'UTC'},
    {'every_seconds': 1, 'timezone': 'UTC'}, {'every_seconds': 1, 'unknown': 1},
])
def test_invalid_or_ambiguous_schedules_are_rejected_before_activation(tmp_path, observer_data, schedule):
    with pytest.raises(ObserverValidationError):
        parse(definition(observer_data, schedule), base_dir=tmp_path)


@pytest.mark.parametrize('kind', ['source', 'route'])
def test_scheduler_detector_and_route_must_match_their_source(tmp_path, observer_data, kind):
    data = definition(observer_data, {'every_seconds': 1})
    if kind == 'source':
        data['detectors'][0] = {'id': 'due', 'type': 'file.change', 'source': 'timer'}
    else:
        data['routes'][0]['event_types'] = ['file.created']
    with pytest.raises(ObserverValidationError):
        parse(data, base_dir=tmp_path)


def source(tmp_path, observer_data, schedule, *, wall=100, mono=10):
    from xdog.eyes.sources.scheduler import SchedulerSource
    parsed = parse(definition(observer_data, schedule), base_dir=tmp_path)
    return SchedulerSource(parsed.sources[0], wall_now=wall, monotonic_now=mono)


def test_one_off_emits_once_and_is_not_rearmed_by_clock_rollback(tmp_path, observer_data):
    timer = source(tmp_path, observer_data, {'at': '2030-01-01T00:00:00Z'},
                   wall=timestamp('2029-12-31T23:59:59Z'))
    due = timestamp('2030-01-01T00:00:00Z')
    assert not timer.poll(wall_now=due - .1, monotonic_now=11).occurrences
    fired = timer.poll(wall_now=due + .1, monotonic_now=12)
    assert fired.completed and len(fired.occurrences) == 1
    evidence = fired.occurrences[0]
    assert evidence.schedule_id == 'timer' and evidence.scheduled_at == due and evidence.observed_at == due + .1
    assert evidence.occurrence_id and not hasattr(evidence, 'path')
    assert not timer.poll(wall_now=due - 5, monotonic_now=13).occurrences
    assert not timer.poll(wall_now=due + 20, monotonic_now=14).occurrences


def test_past_one_off_is_rejected_when_starting_not_when_validating(tmp_path, observer_data):
    with pytest.raises(ValueError, match='future'):
        source(tmp_path, observer_data, {'at': '1970-01-01T00:00:01Z'})


def test_interval_uses_monotonic_time_and_coalesces_late_wakeups(tmp_path, observer_data):
    timer = source(tmp_path, observer_data, {'every_seconds': 1})
    assert not timer.poll(wall_now=10000, monotonic_now=10.5).occurrences
    first = timer.poll(wall_now=10001, monotonic_now=11).occurrences[0]
    assert first.scheduled_at == 10001
    late = timer.poll(wall_now=10003.5, monotonic_now=13.5).occurrences
    assert len(late) == 1 and late[0].scheduled_at == 10003
    assert late[0].occurrence_id != first.occurrence_id
    assert not timer.poll(wall_now=10003.5, monotonic_now=13.5).occurrences
    # A wall-clock rollback does not reset or accelerate the interval.
    assert not timer.poll(wall_now=50, monotonic_now=13.9).occurrences
    assert timer.poll(wall_now=51, monotonic_now=14).occurrences


def test_pause_skips_occurrences_without_a_resume_burst(tmp_path, observer_data):
    timer = source(tmp_path, observer_data, {'every_seconds': 1})
    assert not timer.poll(wall_now=110, monotonic_now=20, suspended=True).occurrences
    assert not timer.poll(wall_now=110, monotonic_now=20).occurrences
    assert len(timer.poll(wall_now=111, monotonic_now=21).occurrences) == 1


def test_cron_honors_zone_dst_and_does_not_repeat_after_rollback(tmp_path, observer_data):
    timer = source(tmp_path, observer_data, {'cron': '0 9 * * *', 'timezone': 'America/New_York'},
                   wall=timestamp('2026-03-07T14:00:00Z'))
    due = timestamp('2026-03-08T13:00:00Z')
    assert not timer.poll(wall_now=due - 1, monotonic_now=11).occurrences
    event, = timer.poll(wall_now=due, monotonic_now=12).occurrences
    assert event.scheduled_at == due and event.timezone == 'America/New_York'
    assert not timer.poll(wall_now=due - 3600, monotonic_now=13).occurrences
    assert not timer.poll(wall_now=due, monotonic_now=14).occurrences


def test_cron_late_wakeup_emits_only_the_latest_due_occurrence(tmp_path, observer_data):
    timer = source(tmp_path, observer_data, {'cron': '* * * * *', 'timezone': 'UTC'},
                   wall=timestamp('2030-01-01T00:00:00Z'))
    late = timestamp('2030-01-01T00:05:30Z')
    event, = timer.poll(wall_now=late, monotonic_now=11).occurrences
    assert event.scheduled_at == timestamp('2030-01-01T00:05:00Z')
    assert not timer.poll(wall_now=late, monotonic_now=12).occurrences


def test_packaged_scheduler_definition_and_skill_examples_use_real_loader(tmp_path):
    import json
    import re
    from importlib.resources import files

    root = files('xdog.eyes')
    data = json.loads(root.joinpath('examples/scheduled-review.json').read_text())
    parsed = parse(data, base_dir=tmp_path)
    assert parsed.sources[0].type == 'scheduler'
    assert parsed.sources[0].schedule.timezone == 'Asia/Shanghai'
    body = root.joinpath('skills/eyes/SKILL.md').read_text()
    definitions = [parse(json.loads(block), base_dir=tmp_path)
                   for block in re.findall(r'```json\s*(.*?)```', body, re.S)]
    assert any(source.type == 'scheduler' for item in definitions for source in item.sources)


def test_cron_microsecond_before_next_minute_never_selects_a_future_tick(tmp_path, observer_data):
    timer = source(tmp_path, observer_data, {'cron': '* * * * *', 'timezone': 'UTC'},
                   wall=timestamp('2030-01-01T00:00:00Z'))
    late = timestamp('2030-01-01T00:02:00Z') - .00000025
    event, = timer.poll(wall_now=late, monotonic_now=11).occurrences
    assert event.scheduled_at == timestamp('2030-01-01T00:01:00Z')
    next_event, = timer.poll(wall_now=timestamp('2030-01-01T00:02:00Z'), monotonic_now=12).occurrences
    assert next_event.scheduled_at == timestamp('2030-01-01T00:02:00Z')
