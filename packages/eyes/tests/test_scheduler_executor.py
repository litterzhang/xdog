"""Scheduler occurrences use the existing router, bounded delivery and runtime lifecycle."""
import asyncio
from copy import deepcopy

import pytest
from xdog.eyes.events import DeliveryResult
from xdog.eyes.loader import definition_data, parse
from xdog.eyes.testing import FakeClock

from packages.eyes.tests.test_acp_unix import unix_peer as sdk_peer
from packages.eyes.tests.test_scheduler_source import definition, timestamp

unix_peer = sdk_peer


@pytest.fixture
async def observers(isolated_daemon_directory):
    from xdog.eyes.daemon import DaemonObservers
    from xdog.eyes.local_control import daemon_location

    value = DaemonObservers(daemon_location(isolated_daemon_directory))
    await value.open()
    try:
        yield value
    finally:
        await value.aclose()


@pytest.mark.parametrize('schedule,count', [
    ({'every_seconds': .2}, 3),
    ({'at': '2030-01-01T00:00:01Z'}, 1),
    ({'cron': '* * * * *', 'timezone': 'UTC'}, 2),
])
async def test_due_events_reach_existing_routes_without_file_scans(tmp_path, observer_data, schedule, count):
    from xdog.eyes.executor import observe

    data = definition(observer_data, schedule)
    data['routes'].append({**deepcopy(data['routes'][0]), 'id': 'second'})
    clock = FakeClock(limit=125)
    base = timestamp('2030-01-01T00:00:00Z')
    cancel = asyncio.Event()
    deliveries, statuses = [], []

    async def forbidden_scan(source):
        pytest.fail('Scheduler-only observer must never scan files')

    async def deliver(value):
        deliveries.append(value)
        return DeliveryResult('succeeded')

    def emit(event):
        statuses.append(event)
        if sum(e.kind == 'delivery_succeeded' for e in statuses) == count * 2:
            cancel.set()

    await observe(parse(data, base_dir=tmp_path), registration_id='registration', scanner=forbidden_scan,
                  deliver=deliver, emit=emit, clock=clock.now, wall_clock=lambda: base + clock.now(),
                  sleep=clock.sleep, cancel=cancel)
    assert len(deliveries) == count * 2
    assert {item.route_id for item in deliveries} == {'review', 'second'}
    events = [item.events[0] for item in deliveries if item.route_id == 'review']
    assert [event.type for event in events] == ['scheduler.due'] * count
    assert len({event.evidence[0].occurrence_id for event in events}) == count
    assert any(event.kind == 'watching' and event.source_id == 'timer' for event in statuses)


async def test_one_off_stays_completed_and_does_not_retrigger(tmp_path, observer_data):
    from xdog.eyes.executor import observe

    clock = FakeClock(limit=5)
    cancel = asyncio.Event()
    deliveries = []
    base = timestamp('2030-01-01T00:00:00Z')

    async def sleep(seconds):
        await clock.sleep(seconds)
        if clock.now() >= 3:
            cancel.set()

    async def deliver(value):
        deliveries.append(value)
        return DeliveryResult('succeeded')

    async def scan(source):
        raise AssertionError('Not a file source')

    await observe(parse(definition(observer_data, {'at': '2030-01-01T00:00:01Z'}), base_dir=tmp_path),
                  registration_id='once', scanner=scan, deliver=deliver, emit=lambda event: None,
                  clock=clock.now, wall_clock=lambda: base + clock.now(), sleep=sleep, cancel=cancel)
    assert len(deliveries) == 1


async def test_health_pause_skips_ticks_and_preserves_a_due_delivery_identity(tmp_path, observer_data):
    from xdog.eyes.executor import observe

    clock = FakeClock(limit=4)
    cancel, suspended = asyncio.Event(), asyncio.Event()
    suspended.set()
    deliveries = []

    async def sleep(seconds):
        await clock.sleep(seconds)
        if clock.now() >= 1.05:
            suspended.clear()

    async def deliver(value):
        deliveries.append(value)
        cancel.set()
        return DeliveryResult('succeeded')

    async def scan(source):
        raise AssertionError('Not a file source')

    await observe(parse(definition(observer_data, {'every_seconds': .2}), base_dir=tmp_path),
                  registration_id='paused', scanner=scan, deliver=deliver, emit=lambda event: None,
                  clock=clock.now, wall_clock=lambda: 100 + clock.now(), sleep=sleep, cancel=cancel,
                  suspended=suspended)
    assert len(deliveries) == 1 and deliveries[0].events[0].evidence[0].scheduled_at >= 101


async def test_daemon_rejects_past_at_before_connecting_any_handler(tmp_path, observer_data, observers, monkeypatch):
    def forbidden_handler(*args):
        pytest.fail('An expired one-off must be refused before allocating handlers')

    monkeypatch.setattr('xdog.eyes.handlers.registry.HandlerRegistry.create', forbidden_handler)
    with pytest.raises(ValueError, match='future'):
        await observers.dispatch('register', {'owner': 'test', 'definition': definition_data(parse(
            definition(observer_data, {'at': '2000-01-01T00:00:00Z'}), base_dir=tmp_path))})
    assert not observers.active
    assert await observers.store.rows() == []


@pytest.mark.parametrize('mode', ['at', 'interval', 'cron'])
async def test_daemon_scheduler_routes_through_official_unix_acp(
        tmp_path, observer_data, unix_peer, mode, observers):
    path, events, gate, writers = unix_peer
    wall = [timestamp('2030-01-01T00:00:00Z')]
    schedule = ({'at': '2030-01-01T00:00:01Z'} if mode == 'at' else {'every_seconds': .1}
                if mode == 'interval' else {'cron': '* * * * *', 'timezone': 'UTC'})
    data = definition(observer_data, schedule)
    data['handlers'][0].update(connection={'transport': 'unix', 'path': str(path)}, session='target')
    observers.wall_clock = lambda: wall[0]
    _, accepted = await observers.dispatch('register', {
        'owner': 'test', 'definition': definition_data(parse(data, base_dir=tmp_path)),
    })
    identifier = accepted['registration_id']
    async with asyncio.timeout(5):
        while not observers.active[identifier].ready:
            await asyncio.sleep(.01)
    try:
        wall[0] += 60 if mode == 'cron' else 1
        async with asyncio.timeout(5):
            while not any(event[0] == 'prompt' for event in events):
                await asyncio.sleep(.01)
        prompt = next(event[1] for event in events if event[0] == 'prompt')
        assert prompt[0] == 'target'
        payload = prompt[2]['xdog']['evidence'][0]
        assert payload['type'] == 'scheduler.due' and payload['source_id'] == 'timer'
        assert payload['evidence'][0]['schedule_id'] == 'timer'
        assert 'path' not in payload['evidence'][0] and 'root' not in payload['evidence'][0]
        async with asyncio.timeout(5):
            while observers.active[identifier].routes.get('review') != 'delivery_succeeded':
                await asyncio.sleep(.01)
        if mode == 'at':
            assert observers.active[identifier].sources['timer'] == 'completed'
            status, duplicate = await observers.dispatch('register', {
                'owner': 'test', 'definition': definition_data(parse(data, base_dir=tmp_path)),
            })
            assert status == 'duplicate' and duplicate['registration_id'] == identifier  # No rearm after expiry.
    finally:
        await observers.dispatch('cancel', {'owner': 'test', 'registration_id': identifier})
    async with asyncio.timeout(3):
        while writers:
            await asyncio.sleep(.01)


async def test_mixed_file_and_scheduler_sources_keep_distinct_evidence(tmp_path, observer_data):
    from xdog.eyes.executor import observe
    from xdog.eyes.models import FileState
    from xdog.eyes.sources.file import FileSnapshot

    observer_data['sources'][0]['poll_interval_ms'] = 50
    observer_data['detectors'][0].update(debounce_ms=1, max_batch_delay_ms=1)
    observer_data['sources'].append({'id': 'timer', 'type': 'scheduler', 'schedule': {'every_seconds': .2}})
    observer_data['detectors'].append({'id': 'due', 'type': 'scheduler.due', 'source': 'timer'})
    observer_data['routes'].append({'id': 'timer-route', 'detector': 'due', 'handler': 'notify',
                                    'event_types': ['scheduler.due']})
    clock = FakeClock(limit=3)
    cancel = asyncio.Event()
    scans, deliveries = [], []

    async def scan(source):
        scans.append(source.id)
        return FileSnapshot(()) if len(scans) == 1 else FileSnapshot((('a.py', FileState('hash', 4)),))

    async def deliver(value):
        deliveries.append(value)
        if {item.route_id for item in deliveries} == {'review', 'timer-route'}:
            cancel.set()
        return DeliveryResult('succeeded')

    await observe(parse(observer_data, base_dir=tmp_path), registration_id='mixed', scanner=scan,
                  deliver=deliver, emit=lambda event: None, clock=clock.now, wall_clock=lambda: 100 + clock.now(),
                  sleep=clock.sleep, cancel=cancel)
    assert set(scans) == {'files'}
    file = next(d for d in deliveries if d.route_id == 'review').events[0].evidence[0]
    timer = next(d for d in deliveries if d.route_id == 'timer-route').events[0].evidence[0]
    assert file.path == 'a.py' and timer.schedule_id == 'timer'


def test_scheduler_evidence_is_bounded_without_fabricating_file_paths():
    from xdog.eyes import SchedulerEvidence
    from xdog.eyes.deliveries import Delivery
    from xdog.eyes.executor import _bounded
    from xdog.eyes.models import Event

    evidence = tuple(SchedulerEvidence('timer', float(i), float(i), str(i), 'UTC') for i in range(257))
    event = Event('event', 'timer', 'due', 'scheduler.due', 300, evidence)
    assert not _bounded(Delivery('id', 'registration', 'route', (event,)))


async def test_scheduler_enqueue_survives_no_owner_then_uses_existing_sdk_batch(
    tmp_path, observer_data, host_skill_repo, assert_host_goal_discoverable, observers,
):
    import tempfile
    from pathlib import Path

    from xdog.agent import Agent, AgentConfig
    from xdog.agent.session_input import TYPE
    from xdog.ai.types import AssistantMessage, TextContent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.acp.ingress import IngressConfig, IngressServer
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.sessions.inbox_store import InboxStore
    from xdog.coding.core.settings_manager import SettingsManager

    manager = SessionManager(tmp_path / 'sessions')
    session_data = manager.create_session(model='test', working_dir=tmp_path)
    store = InboxStore(manager._dir, tmp_path / 'inbox')
    wall = [timestamp('2030-01-01T00:00:00Z')]
    calls = []

    def model(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='Timed review complete'),)))

    with tempfile.TemporaryDirectory(prefix='eyes-timer-') as private:
        ingress = await IngressServer.start(IngressConfig(manager._dir, store.root, Path(private) / 'acp.sock'))
        data = definition(observer_data, {'at': '2030-01-01T00:00:01Z'})
        data['handlers'][0].update(connection={'transport': 'unix', 'path': str(ingress.config.address)},
                                  session=session_data.session_id, delivery='enqueue')
        observers.wall_clock = lambda: wall[0]
        identifier = None
        session = None
        try:
            _, accepted = await observers.dispatch('register', {
                'owner': 'test', 'definition': definition_data(parse(data, base_dir=tmp_path)),
            })
            identifier = accepted['registration_id']
            async with asyncio.timeout(5):
                while not observers.active[identifier].ready:
                    await asyncio.sleep(.01)
            wall[0] += 2
            async with asyncio.timeout(5):
                while not store.records(session_data.session_id, tmp_path):
                    await asyncio.sleep(.01)
            row, = store.records(session_data.session_id, tmp_path)
            assert row.status == 'pending' and not calls
            assert row.message.evidence[0]['type'] == 'scheduler.due'
            assert not manager.load_session(session_data.session_id).messages
            session = AgentSession(agent=Agent(model, config=AgentConfig(model='test'), skills=host_skill_repo),
                session_data=manager.load_session(session_data.session_id), session_manager=manager,
                settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
                bash=None, tool_repo=None)
            response = await session.send_message('Process the retained scheduled observation')
            assert response.content == (TextContent(text='Timed review complete'),)
            assert len(calls) == 1 and len(calls[0][1].messages) == 2
            assert_host_goal_discoverable(calls[0][1])
            row, = store.records(session_data.session_id, tmp_path)
            assert row.status == 'succeeded'
            canonical, = [m for m in session.messages if getattr(m, 'type', '') == TYPE]
            assert canonical.data['evidence'][0]['type'] == 'scheduler.due'
            assert not session.permissions.has_pending_requests
        finally:
            if identifier is not None:
                await observers.dispatch('cancel', {'owner': 'test', 'registration_id': identifier})
            if session is not None:
                session.dispose()
            await ingress.aclose()


async def test_interval_first_period_begins_after_receivers_are_ready(tmp_path, observer_data, unix_peer, observers):
    path, events, gate, writers = unix_peer
    gate.clear()
    mono = [0.0]
    data = definition(observer_data, {'every_seconds': 1})
    data['handlers'][0].update(connection={'transport': 'unix', 'path': str(path)}, session='target')
    observers.clock = lambda: mono[0]
    observers.wall_clock = lambda: 100.0
    _, accepted = await observers.dispatch('register', {
        'owner': 'test', 'definition': definition_data(parse(data, base_dir=tmp_path)),
    })
    identifier = accepted['registration_id']
    try:
        async with asyncio.timeout(3):
            while not any(item[0] == 'initialize' for item in events):
                await asyncio.sleep(.01)
        assert not observers.active[identifier].ready
        mono[0] = 10  # Receiver negotiation took ten virtual seconds.
        gate.set()
        async with asyncio.timeout(3):
            while not observers.active[identifier].ready:
                await asyncio.sleep(.01)
        await asyncio.sleep(.12)
        assert not any(item[0] == 'prompt' for item in events), 'No immediate interval catch-up at activation'
        mono[0] = 11
        async with asyncio.timeout(3):
            while not any(item[0] == 'prompt' for item in events):
                await asyncio.sleep(.01)
    finally:
        gate.set()
        await observers.dispatch('cancel', {'owner': 'test', 'registration_id': identifier})


async def test_scheduler_enqueue_reaches_existing_claw_group_without_tui(tmp_path, observer_data, observers):
    import tempfile
    from pathlib import Path

    from xdog.ai.types import AssistantMessage, TextContent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.acp.server import ClawAcpServer
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.inputs.models import InputScope
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group, GroupConfig

    calls = []

    def model(model_id, context, options=None):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='Claw timed review'),)))

    orch = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/model', stream_fn=model)
    orch.register_group(Group(id='main', workspace=str(tmp_path), enabled_tools=(),
                              config=GroupConfig()))
    with tempfile.TemporaryDirectory(prefix='eyes-claw-timer-') as private:
        server = await ClawAcpServer.start(orch, Path(private) / 'acp.sock')
        wall = [timestamp('2030-01-01T00:00:00Z')]
        data = definition(observer_data, {'at': '2030-01-01T00:00:01Z'})
        data['handlers'][0].update(connection={'transport': 'unix', 'path': str(server.path)},
                                  session='group:main', delivery='enqueue')
        observers.wall_clock = lambda: wall[0]
        identifier = None
        try:
            _, accepted = await observers.dispatch('register', {
                'owner': 'test', 'definition': definition_data(parse(data, base_dir=tmp_path)),
            })
            identifier = accepted['registration_id']
            async with asyncio.timeout(5):
                while not observers.active[identifier].ready:
                    await asyncio.sleep(.01)
            wall[0] += 2
            async with asyncio.timeout(5):
                while True:
                    rows = await server.backend.inputs.store.records(InputScope('main', tmp_path))
                    if rows and rows[0].status == 'succeeded':
                        break
                    await asyncio.sleep(.01)
            assert len(rows) == 1 and len(calls) == 1
            assert rows[0].envelope.message.evidence[0]['type'] == 'scheduler.due'
            assert rows[0].session_id == orch.get_new_session_id('main')
        finally:
            if identifier is not None:
                await observers.dispatch('cancel', {'owner': 'test', 'registration_id': identifier})
            await server.close()
            await orch.stop()


async def test_suspended_one_off_is_completed_without_delivery_or_rearm(tmp_path, observer_data):
    from xdog.eyes.executor import observe

    clock = FakeClock(limit=5)
    cancel, suspended = asyncio.Event(), asyncio.Event()
    suspended.set()
    deliveries, statuses = [], []

    async def sleep(seconds):
        await clock.sleep(seconds)
        if clock.now() >= 2:
            suspended.clear()
        if clock.now() >= 3:
            cancel.set()

    async def forbidden_scan(source):
        pytest.fail('Scheduler-only observer must never scan files')

    async def deliver(value):
        deliveries.append(value)
        return DeliveryResult('succeeded')

    await observe(parse(definition(observer_data, {'at': '2030-01-01T00:00:01Z'}), base_dir=tmp_path),
                  registration_id='paused-once', scanner=forbidden_scan, deliver=deliver,
                  emit=statuses.append, clock=clock.now,
                  wall_clock=lambda: timestamp('2030-01-01T00:00:00Z') + clock.now(),
                  sleep=sleep, cancel=cancel, suspended=suspended)
    assert not deliveries
    assert sum(event.kind == 'source_completed' for event in statuses) == 1
