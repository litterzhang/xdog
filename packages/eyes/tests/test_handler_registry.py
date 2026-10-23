"""A genuine second handler requires no ACP fields, SDK, or runtime branch."""
import asyncio
import json
from dataclasses import dataclass

import pytest
from xdog.eyes.loader import definition_data, parse


@pytest.fixture
def recording(tmp_path):
    from xdog.eyes.handlers.contracts import HandlerSnapshot
    from xdog.eyes.handlers.registry import builtin_handlers
    from xdog.eyes.models import HandlerDef

    @dataclass(frozen=True)
    class RecordDef(HandlerDef):
        label: str

    class Recorder:
        def __init__(self, definition):
            self.definition = definition
            self.calls = []
            self.state = 'new'
            self.deliveries = []

        async def start(self):
            self.calls.append('start')
            self.state = 'ready'

        def validate(self, delivery):
            if sum(len(event.evidence) for event in delivery.events) > 5:
                raise ValueError('record handler full')

        async def deliver(self, delivery):
            from xdog.eyes.events import DeliveryResult
            self.validate(delivery)
            self.deliveries.append(delivery)
            return DeliveryResult('succeeded')

        def snapshot(self):
            return HandlerSnapshot(self.state, {'label': self.definition.label, 'count': len(self.deliveries)})

        async def check(self):
            if self.state != 'ready':
                raise ConnectionError('recorder is closed')

        async def resume(self):
            self.calls.append('resume')
            return True

        async def close(self):
            self.calls.append('close')
            self.state = 'closed'

    def parser(value, path, base_dir):
        if not isinstance(value, dict) or set(value) != {'id', 'type', 'label'}:
            raise ValueError('record fields invalid')
        if not isinstance(value['label'], str) or not value['label']:
            raise ValueError('record label required')
        return RecordDef(value['id'], 'record', value['label'])

    instances = []

    def factory(definition):
        instance = Recorder(definition)
        instances.append(instance)
        return instance

    registry = builtin_handlers()
    registry.register('record', parse_definition=parser, create=factory)
    return registry, instances, parser, factory


@pytest.fixture
async def observers(isolated_daemon_directory, recording, monkeypatch):
    from xdog.eyes.daemon import DaemonObservers
    from xdog.eyes.local_control import daemon_location

    registry, _, _, _ = recording
    monkeypatch.setattr('xdog.eyes.handlers.registry.builtin_handlers', lambda: registry)
    monkeypatch.setattr('xdog.eyes.daemon.builtin_handlers', lambda: registry)
    value = DaemonObservers(daemon_location(isolated_daemon_directory))
    await value.open()
    try:
        yield value
    finally:
        await value.aclose()


def record_data(data):
    data['handlers'] = [{'id': 'recorder', 'type': 'record', 'label': 'audit'}]
    data['routes'][0]['handler'] = 'recorder'
    return data


def test_registry_roundtrip_without_any_acp_configuration(tmp_path, observer_data, recording):
    from xdog.eyes.loader import definition_data, parse

    registry, instances, _, _ = recording
    data = record_data(observer_data)
    parsed = parse(data, base_dir=tmp_path, handler_registry=registry)
    serialized = definition_data(parsed)
    assert serialized['handlers'] == [{'id': 'recorder', 'type': 'record', 'label': 'audit'}]
    assert parse(serialized, base_dir=tmp_path, handler_registry=registry) == parsed
    assert instances == []  # Validation never activates a handler.


def test_registry_refuses_unknown_duplicate_and_malformed_definitions(tmp_path, observer_data, recording):
    from xdog.eyes.loader import parse

    registry, _, parser, factory = recording
    with pytest.raises(ValueError, match='registered|duplicate'):
        registry.register('record', parse_definition=parser, create=factory)
    data = record_data(observer_data)
    data['handlers'][0]['type'] = 'missing'
    with pytest.raises(ValueError, match='handler|Handler'):
        parse(data, base_dir=tmp_path, handler_registry=registry)
    data['handlers'][0]['type'] = 'record'
    data['handlers'][0]['connection'] = {}
    with pytest.raises(ValueError, match='fields'):
        parse(data, base_dir=tmp_path, handler_registry=registry)


async def test_daemon_runs_non_acp_handler_and_uses_its_snapshot(
    tmp_path, observer_data, recording, observers, monkeypatch,
):
    from xdog.eyes.models import FileState
    from xdog.eyes.sources.file import FileSnapshot

    _, instances, _, _ = recording
    scans = []

    async def scan(source):
        scans.append(source.id)
        return FileSnapshot(()) if len(scans) == 1 else FileSnapshot((('a.py', FileState('changed', 3)),))

    data = record_data(observer_data)
    data['sources'][0]['poll_interval_ms'] = 10
    data['detectors'][0].update(debounce_ms=10, max_batch_delay_ms=20)
    monkeypatch.setattr('xdog.eyes.daemon.scan_async', scan)
    _, result = await observers.dispatch('register', {
        'owner': 'test', 'definition': definition_data(parse(data, base_dir=tmp_path)),
    })
    identifier = result['registration_id']
    try:
        async with asyncio.timeout(3):
            while not instances or not instances[0].deliveries:
                await asyncio.sleep(0.01)
        active = observers.active[identifier]
        snapshot = active.handlers['recorder'].snapshot()
        assert snapshot.state == 'ready'
        assert snapshot.details == {'label': 'audit', 'count': 1}
        item = instances[0].deliveries[0]
        assert item.events[0].evidence[0].path == 'a.py'
        assert instances[0].calls == ['start']
    finally:
        await observers.dispatch('cancel', {'owner': 'test', 'registration_id': identifier})
    assert instances[0].calls[-1] == 'close'


async def test_daemon_delivers_two_routes_to_distinct_handler_instances(
    tmp_path, observer_data, recording, observers, monkeypatch,
):
    from xdog.eyes.models import FileState
    from xdog.eyes.sources.file import FileSnapshot

    _, instances, _, _ = recording
    scans = []

    async def scan(source):
        scans.append(source.id)
        return FileSnapshot(()) if len(scans) == 1 else FileSnapshot((('a.py', FileState('new', 3)),))

    data = record_data(observer_data)
    data['handlers'].append({'id': 'other-recorder', 'type': 'record', 'label': 'second'})
    data['routes'].append({**data['routes'][0], 'id': 'other-route', 'handler': 'other-recorder'})
    data['sources'][0]['poll_interval_ms'] = 10
    data['detectors'][0].update(debounce_ms=10, max_batch_delay_ms=20)
    monkeypatch.setattr('xdog.eyes.daemon.scan_async', scan)
    _, result = await observers.dispatch('register', {
        'owner': 'test', 'definition': definition_data(parse(data, base_dir=tmp_path)),
    })
    identifier = result['registration_id']
    try:
        async with asyncio.timeout(3):
            while len(instances) != 2 or not all(handler.deliveries for handler in instances):
                await asyncio.sleep(0.01)
        left, right = [handler.deliveries[0] for handler in instances]
        assert left.events == right.events and left.id != right.id
        assert (left.route_id, right.route_id) == ('review', 'other-route')
    finally:
        await observers.dispatch('cancel', {'owner': 'test', 'registration_id': identifier})
    assert all(handler.calls[-1] == 'close' for handler in instances)


async def test_handler_owned_validation_stops_oversize_delivery_before_send(
    tmp_path, observer_data, recording, observers, monkeypatch,
):
    """Removing runtime's validate injection must attempt six paths and fail this test."""
    from xdog.eyes.models import FileState
    from xdog.eyes.sources.file import FileSnapshot

    _, instances, _, _ = recording
    scans = []

    async def scan(source):
        scans.append(source.id)
        return FileSnapshot(()) if len(scans) == 1 else FileSnapshot(
            tuple((f'{i}.py', FileState('new', 3)) for i in range(6)))

    data = record_data(observer_data)
    data['sources'][0]['poll_interval_ms'] = 10
    data['detectors'][0].update(debounce_ms=10, max_batch_delay_ms=20)
    monkeypatch.setattr('xdog.eyes.daemon.scan_async', scan)
    _, result = await observers.dispatch('register', {
        'owner': 'test', 'definition': definition_data(parse(data, base_dir=tmp_path)),
    })
    identifier = result['registration_id']
    try:
        async with asyncio.timeout(3):
            while observers.active[identifier].routes.get('review') != 'paused':
                await asyncio.sleep(0.01)
        assert instances[0].deliveries == []
        row, = await observers.store.rows('test')
        assert json.loads(row['checkpoint'])['overflowed']
        with pytest.raises(ValueError, match='cannot be resumed'):
            await observers.dispatch('owner-resume', {'owner': 'test', 'registration_id': identifier})
    finally:
        await observers.dispatch('cancel', {'owner': 'test', 'registration_id': identifier})


@pytest.mark.parametrize('recoverable', [False, True])
async def test_common_health_check_and_resume_need_no_acp_attributes(
    tmp_path, observer_data, recording, observers, recoverable,
):
    from xdog.eyes.handlers.contracts import HandlerHealthError

    _, instances, _, _ = recording
    _, result = await observers.dispatch('register', {
        'owner': 'test', 'definition': definition_data(parse(record_data(observer_data), base_dir=tmp_path)),
    })
    identifier = result['registration_id']
    async with asyncio.timeout(3):
        while not observers.active[identifier].ready:
            await asyncio.sleep(.01)
    original = observers.active[identifier]

    async def check():
        raise HandlerHealthError('record storage temporarily unavailable', recoverable=recoverable)

    instances[0].check = check
    await observers.health()
    _, status = await observers.dispatch('owner-status', {'owner': 'test', 'registration_id': identifier})
    assert status['state'] == ('paused' if recoverable else 'needs-review')
    assert status['error'] == 'record storage temporarily unavailable'
    assert original.task.done() and instances[0].state == 'closed'
    if recoverable:
        # Health recovery alone is not authority to restart collection.
        await observers.health()
        assert observers.active[identifier] is original
        await observers.dispatch('owner-resume', {'owner': 'test', 'registration_id': identifier})
        async with asyncio.timeout(3):
            while not observers.active[identifier].ready:
                await asyncio.sleep(.01)
        assert observers.active[identifier] is not original
        assert len(instances) == 2 and instances[1].calls == ['start']
    else:
        with pytest.raises(ValueError, match='cannot be resumed'):
            await observers.dispatch('owner-resume', {'owner': 'test', 'registration_id': identifier})
        assert len(instances) == 1


async def test_custom_handler_explicit_resume_retries_the_same_delivery(
    tmp_path, observer_data, recording, observers, monkeypatch,
):
    from xdog.eyes.events import DeliveryResult
    from xdog.eyes.models import FileState
    from xdog.eyes.sources.file import FileSnapshot

    _, instances, _, _ = recording
    changed = asyncio.Event()

    async def scan(source):
        return FileSnapshot((('a.py', FileState('new', 3)),)) if changed.is_set() else FileSnapshot(())

    monkeypatch.setattr('xdog.eyes.daemon.scan_async', scan)
    data = record_data(observer_data)
    data['sources'][0]['poll_interval_ms'] = 10
    data['detectors'][0].update(debounce_ms=10, max_batch_delay_ms=20)
    _, result = await observers.dispatch('register', {
        'owner': 'test', 'definition': definition_data(parse(data, base_dir=tmp_path)),
    })
    identifier = result['registration_id']
    async with asyncio.timeout(3):
        while observers.active[identifier].sources.get('files') != 'watching':
            await asyncio.sleep(.01)
    handler = instances[0]
    attempts = []

    async def deliver(value):
        attempts.append(value)
        return DeliveryResult('paused', 'record storage offline') if len(attempts) == 1 else DeliveryResult('succeeded')

    handler.deliver = deliver
    changed.set()
    async with asyncio.timeout(3):
        while observers.active[identifier].routes.get('review') != 'paused':
            await asyncio.sleep(.01)
        # Resume is based on the committed paused delivery, not just telemetry.
        while True:
            row, = await observers.store.rows('test')
            if json.loads(row['checkpoint'])['pending']['review']['paused']:
                break
            await asyncio.sleep(.01)
    await observers.dispatch('owner-resume', {'owner': 'test', 'registration_id': identifier})
    async with asyncio.timeout(3):
        while observers.active[identifier].routes.get('review') != 'delivery_succeeded':
            await asyncio.sleep(.01)
    assert handler.calls == ['start', 'resume']
    assert len(instances) == 1 and len(attempts) == 2
    assert attempts[0] == attempts[1]  # No new identity or reconstructed evidence.
