"""Host-independent durable scheduling, with fully controlled wall/monotonic clocks."""
import asyncio
import json
from copy import deepcopy
from dataclasses import replace

import pytest
from xdog.eyes.loader import parse
from xdog.eyes.managed_scheduler import (
    SchedulerClocks,
    initial_managed_state,
    restore_pending_delivery,
    run_managed_scheduler,
)
from xdog.eyes.sources.scheduler import SchedulerSource


class Clock:
    def __init__(self, wall=100., mono=10.):
        self.wall = wall
        self.mono = mono
        self.sleepers = set()

    async def sleep(self, seconds):
        future = asyncio.get_running_loop().create_future()
        self.sleepers.add(future)
        try:
            await future
        finally:
            self.sleepers.discard(future)

    def advance(self, seconds, *, wall=None):
        self.mono += seconds
        self.wall = self.wall + seconds if wall is None else wall
        for future in tuple(self.sleepers):
            if not future.done():
                future.set_result(None)

    def clocks(self):
        return SchedulerClocks(lambda: self.wall, lambda: self.mono, self.sleep)


async def until(predicate):
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError('actor did not reach expected state')


def definition(tmp_path, observer_data, schedule=None):
    data = deepcopy(observer_data)
    data['sources'] = [{'id': 'timer', 'type': 'scheduler', 'schedule': schedule or {'every_seconds': 1}}]
    data['detectors'] = [{'id': 'due', 'type': 'scheduler.due', 'source': 'timer'}]
    data['routes'][0].update(detector='due', event_types=['scheduler.due'])
    return parse(data, base_dir=tmp_path)


class Host:
    def __init__(self):
        self.saved = []
        self.sent = []
        self.gate = asyncio.Event()
        self.delivery_cancelled = False
        self.fail_save = False

    async def save(self, state):
        if self.fail_save:
            raise OSError('disk full')
        self.saved.append(json.loads(json.dumps(state, allow_nan=False)))

    async def deliver(self, delivery):
        assert self.saved[-1]['pending']['id'] == delivery.id
        self.sent.append(delivery)
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.delivery_cancelled = True
            raise

    def start(self, definition, clock, state=None):
        cancel = asyncio.Event()
        task = asyncio.create_task(run_managed_scheduler(definition, 'reg', state, self.save,
                                                        self.deliver, cancel, clock.clocks()))
        return task, cancel


async def test_prepare_before_send_and_busy_ticks_skip_even_at_ack(tmp_path, observer_data):
    clock, host = Clock(), Host()
    task, cancel = host.start(definition(tmp_path, observer_data), clock)
    await until(lambda: clock.sleepers)
    clock.advance(1)
    await until(lambda: len(host.sent) == 1 and clock.sleepers)
    first = host.sent[0]
    clock.advance(99)
    await until(lambda: host.saved[-1]['cursor']['next_utc'] == 201)
    assert host.saved[-1]['pending']['id'] == first.id and len(host.sent) == 1
    # ACK exactly at a deadline must not queue another occurrence from the busy period.
    clock.advance(1)
    host.gate.set()
    await until(lambda: host.saved[-1]['pending'] is None and clock.sleepers)
    assert host.saved[-1]['cursor']['next_utc'] == 202
    assert len(host.sent) == 1
    host.gate.clear()
    clock.advance(1)
    await until(lambda: len(host.sent) == 2)
    assert host.sent[1].id != first.id
    cancel.set()
    await task
    assert not clock.sleepers and host.delivery_cancelled


async def test_pending_recovery_keeps_full_frozen_identity_and_provenance(tmp_path, observer_data):
    clock, host = Clock(), Host()
    parsed = definition(tmp_path, observer_data)
    task, cancel = host.start(parsed, clock)
    await until(lambda: clock.sleepers)
    clock.advance(1)
    await until(lambda: host.sent)
    original = host.sent[0]
    persisted = deepcopy(host.saved[-1])
    cancel.set()
    await task
    # Different process monotonic epoch; delayed pending must not be replaced by a fresh event.
    restored_clock, restored_host = Clock(1001, 0), Host()
    resumed, stop = restored_host.start(parsed, restored_clock, persisted)
    await until(lambda: restored_host.sent and restored_clock.sleepers)
    assert restored_host.sent == [original]
    assert restored_host.saved[-1]['pending'] == persisted['pending']
    assert restored_host.saved[-1]['cursor']['next_utc'] == 1002
    restored_host.gate.set()
    await until(lambda: restored_host.saved[-1]['pending'] is None)
    assert len(restored_host.sent) == 1
    stop.set()
    await resumed


async def test_one_off_accepted_before_deadline_recovers_past_and_acks_once(tmp_path, observer_data):
    clock, host = Clock(), Host()
    parsed = definition(tmp_path, observer_data, {'at': '1970-01-01T00:01:41Z'})
    task, cancel = host.start(parsed, clock)
    await until(lambda: clock.sleepers)
    accepted = deepcopy(host.saved[-1])
    assert accepted['pending'] is None
    cancel.set()
    await task
    with pytest.raises(ValueError, match='future'):
        await run_managed_scheduler(parsed, 'reg', None, host.save, host.deliver,
                                    asyncio.Event(), Clock(200).clocks())
    recovering, stop = host.start(parsed, Clock(200), accepted)
    await until(lambda: host.sent)
    pending = deepcopy(host.saved[-1])
    assert pending['cursor']['completed']
    stop.set()
    await recovering
    # A blocked one-off remains pending on recovery, even long after its scheduled time.
    new_host = Host()
    replay, stop = new_host.start(parsed, Clock(10000), pending)
    await until(lambda: new_host.sent)
    assert new_host.sent == host.sent
    new_host.gate.set()
    await replay
    committed = new_host.saved[-1]
    assert committed['pending'] is None and committed['cursor']['completed']
    await run_managed_scheduler(parsed, 'reg', committed, new_host.save, new_host.deliver,
                                asyncio.Event(), Clock(20000).clocks())
    assert len(new_host.sent) == 1


@pytest.mark.parametrize('stage', ['accept', 'prepare', 'busy', 'commit'])
async def test_write_failure_halts_without_advancing_delivery(tmp_path, observer_data, stage):
    clock, host = Clock(), Host()
    host.fail_save = stage == 'accept'
    task, cancel = host.start(definition(tmp_path, observer_data), clock)
    if stage != 'accept':
        await until(lambda: clock.sleepers)
        if stage == 'prepare':
            host.fail_save = True
        clock.advance(1)
        if stage in {'busy', 'commit'}:
            await until(lambda: host.sent and clock.sleepers)
            host.fail_save = True
            if stage == 'busy':
                clock.advance(1)
            else:
                host.gate.set()
    with pytest.raises(OSError, match='disk full'):
        await task
    assert len(host.sent) == (1 if stage in {'busy', 'commit'} else 0)
    assert not clock.sleepers
    if stage in {'busy', 'commit'}:
        assert host.saved[-1]['pending'] is not None
    if stage == 'busy':
        assert host.delivery_cancelled


async def test_delivery_failure_freezes_pending_without_retry(tmp_path, observer_data):
    clock, host = Clock(), Host()

    async def fail(delivery):
        host.sent.append(delivery)
        raise ConnectionError('ack lost')

    host.deliver = fail
    task, cancel = host.start(definition(tmp_path, observer_data), clock)
    await until(lambda: clock.sleepers)
    clock.advance(1)
    with pytest.raises(ConnectionError, match='ack lost'):
        await task
    assert len(host.sent) == 1
    assert host.saved[-1]['pending']['id'] == host.sent[0].id
    assert not clock.sleepers


async def test_cancel_interrupts_blocked_save_without_sending(tmp_path, observer_data):
    clock, host = Clock(), Host()
    saving, interrupted = asyncio.Event(), asyncio.Event()

    async def save(state):
        saving.set()
        try:
            await asyncio.Event().wait()
        finally:
            interrupted.set()

    host.save = save
    task, cancel = host.start(definition(tmp_path, observer_data), clock)
    await saving.wait()
    cancel.set()
    await task
    assert interrupted.is_set() and not host.sent


async def test_external_task_cancel_drains_delivery_and_clock(tmp_path, observer_data):
    clock, host = Clock(), Host()
    task, cancel = host.start(definition(tmp_path, observer_data), clock)
    await until(lambda: clock.sleepers)
    clock.advance(1)
    await until(lambda: host.sent)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert host.delivery_cancelled and not clock.sleepers


@pytest.mark.parametrize('corrupt', ['version', 'binding', 'registration', 'unknown', 'nan', 'bool', 'code'])
async def test_state_strict_data_and_binding_rejected_before_callbacks(tmp_path, observer_data, corrupt):
    clock, host = Clock(), Host()
    parsed = definition(tmp_path, observer_data)
    task, cancel = host.start(parsed, clock)
    await until(lambda: clock.sleepers)
    state = deepcopy(host.saved[-1])
    cancel.set()
    await task
    if corrupt == 'version':
        state['version'] = True
    elif corrupt == 'binding':
        state['binding'] = 'other'
    elif corrupt == 'registration':
        state['registration_id'] = 'other'
    elif corrupt == 'unknown':
        state['client'] = 'forbidden'
    elif corrupt == 'nan':
        state['cursor']['next_utc'] = float('nan')
    elif corrupt == 'bool':
        state['cursor']['next_utc'] = True
    else:
        state['cursor']['next_utc'] = lambda: None
    before = deepcopy(host.saved)
    with pytest.raises(ValueError):
        await run_managed_scheduler(parsed, 'reg', state, host.save, host.deliver,
                                    asyncio.Event(), clock.clocks())
    assert host.saved == before and not host.sent


async def test_managed_shape_is_restricted_without_connecting_handlers(tmp_path, observer_data):
    parsed = definition(tmp_path, observer_data)
    invalid = replace(parsed, routes=parsed.routes + parsed.routes)
    host = Host()
    with pytest.raises(ValueError, match='one source'):
        await run_managed_scheduler(invalid, 'reg', None, host.save, host.deliver,
                                    asyncio.Event(), Clock().clocks())
    assert not host.saved and not host.sent


def test_interval_checkpoint_uses_utc_only_for_restart_and_coalesces(tmp_path, observer_data):
    parsed = definition(tmp_path, observer_data)
    timer = SchedulerSource(parsed.sources[0], wall_now=100, monotonic_now=10)
    assert not timer.poll(wall_now=10000, monotonic_now=10.5).occurrences
    checkpoint = timer.export_checkpoint(wall_now=10000, monotonic_now=10.5)
    assert checkpoint['next_utc'] == 10000.5
    restored = SchedulerSource.restore(parsed.sources[0], checkpoint, wall_now=10010.5, monotonic_now=0)
    snapshot = restored.poll(wall_now=10010.5, monotonic_now=0)
    assert len(snapshot.occurrences) == 1
    assert snapshot.occurrences[0].scheduled_at == 10010.5
    assert not restored.poll(wall_now=50000, monotonic_now=.5).occurrences
    assert restored.poll(wall_now=50001, monotonic_now=1).occurrences


def test_cron_checkpoint_preserves_utc_cursor_through_rollback(tmp_path, observer_data):
    parsed = definition(tmp_path, observer_data, {'cron': '* * * * *', 'timezone': 'UTC'})
    timer = SchedulerSource(parsed.sources[0], wall_now=100, monotonic_now=10)
    timer.poll(wall_now=180, monotonic_now=90)
    checkpoint = timer.export_checkpoint(wall_now=180, monotonic_now=90)
    assert checkpoint['next_utc'] == 240
    restored = SchedulerSource.restore(parsed.sources[0], checkpoint, wall_now=120, monotonic_now=0)
    assert not restored.poll(wall_now=180, monotonic_now=60).occurrences
    assert restored.poll(wall_now=240, monotonic_now=120).occurrences[0].scheduled_at == 240
    changed = replace(parsed.sources[0], id='different')
    with pytest.raises(ValueError, match='binding'):
        SchedulerSource.restore(changed, checkpoint, wall_now=240, monotonic_now=0)


def test_checkpoint_and_state_contain_no_handler_command_or_credentials(tmp_path, observer_data):
    parsed = definition(tmp_path, observer_data)
    timer = SchedulerSource(parsed.sources[0], wall_now=100, monotonic_now=10)
    dumped = json.dumps(timer.export_checkpoint(wall_now=100, monotonic_now=10))
    assert 'third-party-acp' not in dumped and 'connection' not in dumped


async def test_prepare_waits_for_atomic_save_and_pending_is_immutable(tmp_path, observer_data):
    from dataclasses import FrozenInstanceError

    clock, host = Clock(), Host()
    prepare, commit = asyncio.Event(), asyncio.Event()
    original_save = host.save

    async def save(state):
        if state['pending'] is not None:
            prepare.set()
            await commit.wait()
        await original_save(state)

    host.save = save
    task, cancel = host.start(definition(tmp_path, observer_data), clock)
    await until(lambda: clock.sleepers)
    clock.advance(1)
    await prepare.wait()
    assert not host.sent and host.saved[-1]['pending'] is None
    commit.set()
    await until(lambda: host.sent)
    delivery = host.sent[0]
    with pytest.raises(FrozenInstanceError):
        delivery.id = 'replacement'
    with pytest.raises(FrozenInstanceError):
        delivery.events[0].id = 'replacement'
    with pytest.raises(FrozenInstanceError):
        delivery.events[0].evidence[0].occurrence_id = 'replacement'
    assert 'third-party-acp' not in json.dumps(host.saved)
    cancel.set()
    await task


async def test_cancel_from_prepare_save_prevents_send(tmp_path, observer_data):
    clock, host = Clock(), Host()
    cancel = asyncio.Event()
    original_save = host.save

    async def save(state):
        await original_save(state)
        if state['pending'] is not None:
            cancel.set()

    task = asyncio.create_task(run_managed_scheduler(definition(tmp_path, observer_data), 'reg', None,
                                                     save, host.deliver, cancel, clock.clocks()))
    await until(lambda: clock.sleepers)
    clock.advance(1)
    await task
    assert not host.sent and host.saved[-1]['pending'] is not None


@pytest.mark.parametrize('field', ['delivery', 'event', 'evidence', 'source', 'registration', 'occurrence'])
async def test_pending_restore_rejects_unknown_fields_and_wrong_provenance(tmp_path, observer_data, field):
    clock, host = Clock(), Host()
    parsed = definition(tmp_path, observer_data)
    task, cancel = host.start(parsed, clock)
    await until(lambda: clock.sleepers)
    clock.advance(1)
    await until(lambda: host.sent)
    state = deepcopy(host.saved[-1])
    cancel.set()
    await task
    pending = state['pending']
    if field == 'delivery':
        pending['client'] = 'invalid'
    elif field == 'event':
        pending['events'][0]['code'] = 'invalid'
    elif field == 'evidence':
        pending['events'][0]['evidence'][0]['credentials'] = 'invalid'
    elif field == 'source':
        pending['events'][0]['source_id'] = 'different'
    elif field == 'registration':
        pending['registration_id'] = 'different'
    else:
        pending['events'][0]['evidence'][0]['occurrence_id'] = ''
    restored_host = Host()
    with pytest.raises(ValueError):
        await run_managed_scheduler(parsed, 'reg', state, restored_host.save, restored_host.deliver,
                                    asyncio.Event(), clock.clocks())
    assert not restored_host.saved and not restored_host.sent


async def test_managed_cron_uses_source_cursor_and_skips_while_busy(tmp_path, observer_data):
    clock, host = Clock(100, 10), Host()
    parsed = definition(tmp_path, observer_data, {'cron': '* * * * *', 'timezone': 'UTC'})
    task, cancel = host.start(parsed, clock)
    await until(lambda: clock.sleepers)
    clock.advance(20)
    await until(lambda: host.sent and clock.sleepers)
    assert host.sent[0].events[0].evidence[0].scheduled_at == 120
    clock.advance(300)
    await until(lambda: host.saved[-1]['cursor']['next_utc'] == 480)
    assert len(host.sent) == 1
    host.gate.set()
    await until(lambda: host.saved[-1]['pending'] is None and clock.sleepers)
    clock.advance(60)
    await until(lambda: len(host.sent) == 2)
    assert host.sent[1].events[0].evidence[0].scheduled_at == 480
    cancel.set()
    await task


async def test_idle_interval_wall_microdrift_does_not_write_on_each_poll(tmp_path, observer_data):
    clock, host = Clock(), Host()
    parsed = definition(tmp_path, observer_data, {'every_seconds': 3600})
    task, cancel = host.start(parsed, clock)
    await until(lambda: clock.sleepers)
    assert len(host.saved) == 1
    cursor = deepcopy(host.saved[0]['cursor'])
    for _ in range(20):
        clock.advance(.2, wall=clock.wall + .200000001)
        await until(lambda: clock.sleepers and all(not future.done() for future in clock.sleepers))
        assert len(host.saved) == 1
    # Even a large wall-clock correction is not an online interval cursor change.
    clock.advance(.2, wall=10000)
    await until(lambda: clock.sleepers and all(not future.done() for future in clock.sleepers))
    assert len(host.saved) == 1 and host.saved[0]['cursor'] == cursor
    cancel.set()
    await task
    assert not clock.sleepers


async def test_create_seed_roundtrip_recovers_expired_accepted_at_once(tmp_path, observer_data):
    parsed = definition(tmp_path, observer_data, {'at': '1970-01-01T00:01:41Z'})
    seed = initial_managed_state(parsed, 'reg', Clock().clocks())
    persisted = json.loads(json.dumps(seed, allow_nan=False))
    assert persisted == seed
    assert persisted['pending'] is None and persisted['cursor']['next_utc'] == 101
    assert restore_pending_delivery(parsed, 'reg', persisted) is None
    # The host has committed create, but no actor ever ran before the process died.
    host = Host()
    task, cancel = host.start(parsed, Clock(200, 0), persisted)
    await until(lambda: host.sent)
    assert host.sent[0].events[0].evidence[0].scheduled_at == 101
    assert host.saved[0] == seed  # actor still performs its required initial save
    host.gate.set()
    await task
    assert host.saved[-1]['pending'] is None
    again, cancel = host.start(parsed, Clock(300, 0), host.saved[-1])
    await again
    assert len(host.sent) == 1
    with pytest.raises(ValueError, match='future'):
        initial_managed_state(parsed, 'reg', Clock(200).clocks())


@pytest.mark.parametrize('schedule', [
    {'every_seconds': 1}, {'cron': '* * * * *', 'timezone': 'UTC'}, {'at': '1970-01-01T00:01:41Z'},
])
async def test_initial_seed_matches_actor_first_save(tmp_path, observer_data, schedule):
    clock, host = Clock(), Host()
    parsed = definition(tmp_path, observer_data, schedule)
    expected = initial_managed_state(parsed, 'reg', clock.clocks())
    task, cancel = host.start(parsed, clock)
    await until(lambda: host.saved)
    assert host.saved[0] == expected
    cancel.set()
    await task


async def test_public_pending_helper_decodes_full_identity_inside_save(tmp_path, observer_data):
    clock, host = Clock(), Host()
    parsed = definition(tmp_path, observer_data)
    restored = []
    original_save = host.save

    async def save(state):
        # Same call boundary used by the host's canonical input + outbox transaction.
        restored.append(restore_pending_delivery(parsed, 'reg', state))
        await original_save(state)

    host.save = save
    task, cancel = host.start(parsed, clock)
    await until(lambda: clock.sleepers)
    assert restored == [None]
    clock.advance(1)
    await until(lambda: host.sent)
    assert restored[-1] == host.sent[0]
    original = host.sent[0]
    persisted = json.loads(json.dumps(host.saved[-1]))
    cancel.set()
    await task
    assert restore_pending_delivery(parsed, 'reg', persisted) == original
    assert restored[-1].events[0].evidence == original.events[0].evidence


@pytest.mark.parametrize('corrupt', ['binding', 'registration', 'cursor', 'pending', 'shape'])
def test_public_pending_helper_strictly_validates_whole_state(tmp_path, observer_data, corrupt):
    parsed = definition(tmp_path, observer_data)
    state = initial_managed_state(parsed, 'reg', Clock().clocks())
    if corrupt == 'binding':
        state['binding'] = 'invalid'
    elif corrupt == 'registration':
        state['registration_id'] = 'invalid'
    elif corrupt == 'cursor':
        state['cursor']['next_utc'] = True
    elif corrupt == 'pending':
        state['pending'] = {'id': 'not-a-complete-delivery'}
    else:
        state['credentials'] = 'not-allowed'
    with pytest.raises(ValueError):
        restore_pending_delivery(parsed, 'reg', state)


@pytest.mark.parametrize('first_cancel', ['task', 'event'])
async def test_repeated_cancellation_joins_gated_delivery_cleanup(tmp_path, observer_data, first_cancel):
    clock, host = Clock(), Host()
    cleanup_started, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_deliver = host.deliver

    async def deliver(delivery):
        try:
            await original_deliver(delivery)
        finally:
            cleanup_started.set()
            await release.wait()
            cleaned.set()

    host.deliver = deliver
    task, cancel = host.start(definition(tmp_path, observer_data), clock)
    await until(lambda: clock.sleepers)
    clock.advance(1)
    await until(lambda: host.sent)
    if first_cancel == 'task':
        task.cancel()
    else:
        cancel.set()
    await cleanup_started.wait()
    # Repeated owner cancellation must not propagate into callback's gated finally.
    for _ in range(3):
        task.cancel()
        for _ in range(3):
            await asyncio.sleep(0)
        assert not task.done() and not cleaned.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set() and not clock.sleepers
    assert host.saved[-1]['pending']['id'] == host.sent[0].id


async def test_save_error_keeps_priority_over_delivery_cleanup_error(tmp_path, observer_data):
    clock, host = Clock(), Host()
    cleanup_started, release = asyncio.Event(), asyncio.Event()
    original_deliver = host.deliver

    async def deliver(delivery):
        try:
            await original_deliver(delivery)
        finally:
            cleanup_started.set()
            await release.wait()
            raise RuntimeError('cleanup error must not hide disk failure')

    host.deliver = deliver
    task, cancel = host.start(definition(tmp_path, observer_data), clock)
    await until(lambda: clock.sleepers)
    clock.advance(1)
    await until(lambda: host.sent and clock.sleepers)
    host.fail_save = True
    clock.advance(1)
    await cleanup_started.wait()
    assert not task.done()
    release.set()
    with pytest.raises(OSError, match='disk full'):
        await task
    assert not clock.sleepers


async def test_pending_commit_refreshes_utc_bridge_without_idle_writes(tmp_path, observer_data):
    clock, host = Clock(), Host()
    task, cancel = host.start(definition(tmp_path, observer_data), clock)
    await until(lambda: clock.sleepers)
    clock.advance(1)
    await until(lambda: host.sent and clock.sleepers)
    writes = len(host.saved)
    clock.advance(.1, wall=1000)
    await until(lambda: clock.sleepers and all(not future.done() for future in clock.sleepers))
    assert len(host.saved) == writes
    host.gate.set()
    await until(lambda: host.saved[-1]['pending'] is None and clock.sleepers)
    assert len(host.saved) == writes + 1
    assert host.saved[-1]['cursor']['next_utc'] == pytest.approx(1000.9)
    cancel.set()
    await task
