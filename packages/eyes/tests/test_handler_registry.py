"""A genuine second handler requires no ACP fields, SDK, or runtime branch."""
import asyncio
from dataclasses import dataclass

import pytest


@pytest.fixture
def recording(tmp_path):
    from xdog.eyes.handlers.contracts import HandlerSnapshot
    from xdog.eyes.handlers.registry import builtin_handlers
    from xdog.eyes.models import HandlerDef

    @dataclass(frozen=True)
    class RecordDef(HandlerDef):
        label: str

    class Recorder:
        def __init__(self, definition, cwd):
            self.definition = definition
            self.cwd = cwd
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

    def factory(definition, cwd):
        instance = Recorder(definition, cwd)
        instances.append(instance)
        return instance

    registry = builtin_handlers()
    registry.register('record', parse_definition=parser, create=factory)
    return registry, instances, parser, factory


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


async def test_runtime_runs_non_acp_handler_and_uses_its_snapshot(tmp_path, observer_data, recording):
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.models import FileState
    from xdog.eyes.runtime import ObserverRuntime
    from xdog.eyes.sources.file import FileSnapshot

    registry, instances, _, _ = recording
    scans = []

    async def scan(source):
        scans.append(source.id)
        return FileSnapshot(()) if len(scans) == 1 else FileSnapshot((('a.py', FileState('changed', 3)),))

    data = record_data(observer_data)
    data['sources'][0]['poll_interval_ms'] = 10
    data['detectors'][0].update(debounce_ms=10, max_batch_delay_ms=20)
    runtime = ObserverRuntime(runtime_location(tmp_path), scanner=scan, handler_registry=registry)
    identifier = (await runtime.start(data))['registration_id']
    try:
        async with asyncio.timeout(3):
            while not instances[0].deliveries:
                await asyncio.sleep(0.01)
        status = (await runtime.status(identifier))['registrations'][0]
        assert status['handlers']['recorder'] == {'state': 'ready', 'label': 'audit', 'count': 1}
        item = instances[0].deliveries[0]
        assert item.events[0].evidence[0].path == 'a.py'
        assert await runtime.resume_entry(identifier)
        assert instances[0].calls == ['start', 'resume']
    finally:
        await runtime.stop_entry(identifier)
    assert instances[0].calls[-1] == 'close'


async def test_runtime_delivers_two_routes_to_distinct_handler_instances(tmp_path, observer_data, recording):
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.models import FileState
    from xdog.eyes.runtime import ObserverRuntime
    from xdog.eyes.sources.file import FileSnapshot

    registry, instances, _, _ = recording
    scans = []

    async def scan(source):
        scans.append(source.id)
        return FileSnapshot(()) if len(scans) == 1 else FileSnapshot((('a.py', FileState('new', 3)),))

    data = record_data(observer_data)
    data['handlers'].append({'id': 'other-recorder', 'type': 'record', 'label': 'second'})
    data['routes'].append({**data['routes'][0], 'id': 'other-route', 'handler': 'other-recorder'})
    data['sources'][0]['poll_interval_ms'] = 10
    data['detectors'][0].update(debounce_ms=10, max_batch_delay_ms=20)
    runtime = ObserverRuntime(runtime_location(tmp_path), scanner=scan, handler_registry=registry)
    identifier = (await runtime.start(data))['registration_id']
    try:
        async with asyncio.timeout(3):
            while not all(handler.deliveries for handler in instances):
                await asyncio.sleep(0.01)
        left, right = [handler.deliveries[0] for handler in instances]
        assert left.events == right.events and left.id != right.id
        assert (left.route_id, right.route_id) == ('review', 'other-route')
    finally:
        await runtime.stop_entry(identifier)
    assert all(handler.calls[-1] == 'close' for handler in instances)


async def test_handler_owned_validation_stops_oversize_delivery_before_send(tmp_path, observer_data, recording):
    """Removing runtime's validate injection must attempt six paths and fail this test."""
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.models import FileState
    from xdog.eyes.runtime import ObserverRuntime
    from xdog.eyes.sources.file import FileSnapshot

    registry, instances, _, _ = recording
    scans = []

    async def scan(source):
        scans.append(source.id)
        return FileSnapshot(()) if len(scans) == 1 else FileSnapshot(
            tuple((f'{i}.py', FileState('new', 3)) for i in range(6)))

    data = record_data(observer_data)
    data['sources'][0]['poll_interval_ms'] = 10
    data['detectors'][0].update(debounce_ms=10, max_batch_delay_ms=20)
    runtime = ObserverRuntime(runtime_location(tmp_path), scanner=scan, handler_registry=registry)
    identifier = (await runtime.start(data))['registration_id']
    try:
        async with asyncio.timeout(3):
            while not runtime.entries[identifier].routes['review'].get('paused'):
                await asyncio.sleep(0.01)
        assert instances[0].deliveries == []
        assert runtime.entries[identifier].routes['review'].get('fatal') is True
    finally:
        await runtime.stop_entry(identifier)


async def test_common_health_check_and_resume_need_no_acp_attributes(tmp_path, observer_data, recording):
    """ACP-specific monitor attribute access incorrectly retires the valid recorder."""
    from xdog.eyes.handlers.contracts import HandlerHealthError
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.runtime import ObserverRuntime

    registry, instances, _, _ = recording
    runtime = ObserverRuntime(runtime_location(tmp_path), handler_registry=registry)
    identifier = (await runtime.start(record_data(observer_data)))['registration_id']
    checked = asyncio.Event()

    async def check():
        checked.set()
        raise HandlerHealthError('record storage temporarily unavailable', recoverable=True)

    instances[0].check = check
    monitor = asyncio.create_task(runtime.monitor())
    try:
        await asyncio.wait_for(checked.wait(), 3)
        entry = runtime.entries[identifier]
        assert not entry.retired and not entry.registration.task.done()
        assert entry.suspended.is_set()
        assert entry.health == {'recorder': 'record storage temporarily unavailable'}
        assert await runtime.resume_entry(identifier)
        assert not entry.health and not entry.suspended.is_set()
    finally:
        runtime.stop.set()
        await asyncio.wait_for(monitor, 3)
        await runtime.stop_entry(identifier)
