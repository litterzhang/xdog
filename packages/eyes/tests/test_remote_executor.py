"""Remote observations use the original durable executor, routing and checkpoint."""
import asyncio
import copy

import pytest
from xdog.eyes.checkpoint import delivery_data, restore_delivery
from xdog.eyes.events import DeliveryResult
from xdog.eyes.executor import observe
from xdog.eyes.loader import parse
from xdog.eyes.sources.records import PollResult, Record, RecordSnapshot
from xdog.eyes.testing import FakeClock

from packages.eyes.tests.test_remote_config import external


class Sampler:
    def __init__(self):
        self.calls = []
        self.closed = False

    async def poll(self, source, state, *, now):
        self.calls.append(copy.deepcopy(state))
        records = () if not state else (Record('new', '{"title":"招聘公告"}', 'https://example.test/new'),)
        return PollResult(RecordSnapshot(records, now), {'last_success': now, 'next_poll_at': now+1}, 1)

    async def close(self):
        self.closed = True


async def test_remote_cursor_detector_and_delivery_commit_before_any_send(tmp_path, observer_data):
    definition = parse(external(observer_data), base_dir=tmp_path)
    sampler, clock, cancel = Sampler(), FakeClock(limit=10), asyncio.Event()
    checkpoints, delivered = [], []

    async def save(value):
        checkpoints.append(copy.deepcopy(value))

    async def deliver(value):
        saved = checkpoints[-1]
        assert saved['remote_sources']['outside']['last_success'] > 0
        assert saved['detectors']['change']['records'][0]['key'] == 'new'
        assert saved['pending']['route']['delivery']['id'] == value.id
        assert saved['pending']['route']['inflight']
        restored = restore_delivery(delivery_data(value), definition, 'registration')
        assert restored == value
        delivered.append(value)
        cancel.set()
        return DeliveryResult('succeeded')

    async def scanner(_):
        pytest.fail('Remote source cannot use the file scanner')

    await observe(definition, registration_id='registration', scanner=scanner, deliver=deliver,
        emit=lambda event: None, clock=clock.now, wall_clock=clock.now, sleep=clock.sleep, cancel=cancel,
        save=save, remote_sampler=sampler)
    assert len(delivered) == 1 and len(sampler.calls) == 2 and sampler.closed
    baseline = next(value['remote_sources']['outside']['last_success'] for value in checkpoints
                    if 'outside' in value['remote_sources'])
    assert sampler.calls[0] == {} and sampler.calls[1]['last_success'] == baseline
    evidence = delivered[0].events[0].evidence[0]
    assert '招聘公告' in evidence.after and evidence.url == 'https://example.test/new'


async def test_failed_checkpoint_cannot_send_or_commit_remote_candidate(tmp_path, observer_data):
    definition = parse(external(observer_data), base_dir=tmp_path)
    sampler, clock = Sampler(), FakeClock(limit=10)
    sent = []

    async def save(value):
        if value['pending']:
            raise OSError('Actual checkpoint write failed')

    async def deliver(value):
        sent.append(value)
        return DeliveryResult('succeeded')

    async def scanner(_):
        raise AssertionError('not file')

    with pytest.raises(OSError, match='checkpoint write failed'):
        await observe(definition, registration_id='registration', scanner=scanner, deliver=deliver,
            emit=lambda event: None, clock=clock.now, wall_clock=clock.now, sleep=clock.sleep,
            cancel=asyncio.Event(), save=save, remote_sampler=sampler)
    assert not sent and sampler.closed


async def test_unknown_delivery_restore_never_rescans_or_replays_remote_work(tmp_path, observer_data):
    definition = parse(external(observer_data), base_dir=tmp_path)
    sampler, clock, cancel = Sampler(), FakeClock(limit=10), asyncio.Event()
    checkpoints = []

    async def save(value):
        checkpoints.append(copy.deepcopy(value))

    async def first_send(value):
        cancel.set()
        return DeliveryResult('outcome-unknown', 'Unknown downstream receipt')

    async def scanner(_):
        raise AssertionError('not file')

    await observe(definition, registration_id='registration', scanner=scanner, deliver=first_send,
        emit=lambda event: None, clock=clock.now, wall_clock=clock.now, sleep=clock.sleep, cancel=cancel,
        save=save, remote_sampler=sampler)
    checkpoint = next(value for value in reversed(checkpoints) if value['pending'])
    assert checkpoint['pending']['route']['inflight']
    restored, stop = Sampler(), asyncio.Event()
    statuses = []
    def emit(event):
        statuses.append(event.kind)
        if event.kind == 'delivery_outcome_unknown':
            stop.set()
    async def never(value):
        pytest.fail('Unknown delivery must not be replayed')
    await observe(definition, registration_id='registration', scanner=scanner, deliver=never,
        emit=emit, clock=clock.now, wall_clock=clock.now, sleep=clock.sleep, cancel=stop,
        checkpoint=checkpoint, remote_sampler=restored)
    assert not restored.calls and restored.closed and 'delivery_outcome_unknown' in statuses


async def test_restart_respects_durable_remote_backoff_and_reports_old_error(tmp_path, observer_data):
    definition = parse(external(observer_data), base_dir=tmp_path)
    sampler, clock, cancel = Sampler(), FakeClock(limit=10), asyncio.Event()
    checkpoint = {'version': 1, 'detectors': {}, 'schedulers': {}, 'pending': {}, 'backlog': {},
                  'overflowed': False, 'remote_sources': {'outside': {
                      'last_success': 99, 'next_poll_at': 1100, 'failures': 1, 'error': 'HTTP status 429'}}}
    events = []
    async def sleep(seconds):
        await clock.sleep(seconds)
        if clock.now() >= 1:
            cancel.set()
    async def never(_):
        pytest.fail('A source/backoff restore must not perform early network or delivery IO')
    await observe(definition, registration_id='registration', scanner=never, deliver=never,
        emit=events.append, clock=clock.now, wall_clock=lambda: 100+clock.now(), sleep=sleep, cancel=cancel,
        checkpoint=checkpoint, remote_sampler=sampler)
    assert not sampler.calls and sampler.closed
    assert any(event.kind == 'source_error' and event.error == 'HTTP status 429' for event in events)
