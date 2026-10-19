"""Embedding owns no sockets, leases, process cwd or idle-exit policy."""
import asyncio
from copy import deepcopy

import pytest
from xdog.eyes.handlers.contracts import HandlerHealthError
from xdog.eyes.service import ObserverService
from xdog.eyes.sources.file import FileSnapshot

from packages.eyes.tests.test_handler_registry import record_data, recording
from packages.eyes.tests.test_managed_scheduler import Clock, until

__all__ = ['recording']


async def test_explicit_cwd_lifecycle_has_no_ipc_or_automatic_restore(tmp_path, observer_data, recording, monkeypatch):
    registry, instances, _, _ = recording
    clock, scans = Clock(), []
    project = tmp_path / 'project'
    project.mkdir()

    def no_ipc(*args, **kwargs):
        raise AssertionError('embedded service must not allocate runtime locations')

    monkeypatch.setattr('xdog.eyes.local_control.runtime_location', no_ipc)

    async def scan(source):
        scans.append(source.root)
        return FileSnapshot(())

    data = record_data(deepcopy(observer_data))
    service = ObserverService(cwd=project, handler_registry=registry, scanner=scan,
                              clock=lambda: clock.mono, sleep=clock.sleep)
    assert (await service.status()) == {'registrations': []}
    assert not instances and not scans
    result = await service.start(data)
    identifier = result['registration_id']
    assert (await service.start(deepcopy(data)))['duplicate']
    await until(lambda: scans)
    assert set(scans) == {project}
    assert instances[0].cwd == project
    clock.advance(.1)
    await until(lambda: service.entries[identifier].sources['files'] == 'watching')
    assert (await service.health())['registrations'][0]['state'] == 'watching'
    # Embedded empty and active services never use CLI's ten-second idle exit.
    clock.advance(100)
    await until(lambda: clock.sleepers)
    assert not service._shutdown.is_set()
    assert await service.resume(identifier)
    assert (await service.stop(identifier))['stopped']
    assert instances[0].state == 'closed'
    assert (await service.status()) == {'registrations': []}
    clock.advance(100)
    await asyncio.sleep(0)
    assert not service._shutdown.is_set()
    await service.aclose()
    await service.aclose()
    assert not clock.sleepers
    assert service._monitor_task.done()
    with pytest.raises(RuntimeError, match='closing'):
        await service.start(data)
    fresh = ObserverService(cwd=project, handler_registry=registry)
    assert (await fresh.status()) == {'registrations': []}
    await fresh.aclose()


async def test_recoverable_health_pauses_until_explicit_resume(tmp_path, observer_data, recording):
    registry, instances, _, _ = recording
    clock, scans = Clock(), []

    async def scan(source):
        scans.append(source.id)
        return FileSnapshot(())

    service = ObserverService(cwd=tmp_path, handler_registry=registry, scanner=scan, auto_health=False,
                              clock=lambda: clock.mono, sleep=clock.sleep)
    identifier = (await service.start(record_data(observer_data)))['registration_id']
    await until(lambda: scans)
    handler = instances[0]

    async def disconnected():
        raise HandlerHealthError('offline', recoverable=True)

    handler.check = disconnected
    row = (await service.health())['registrations'][0]
    assert row['state'] == 'paused'
    assert service.entries[identifier].suspended.is_set()
    assert handler.state == 'ready'  # recoverable health never destroys the receiver

    async def healthy():
        pass

    handler.check = healthy
    await service.health()
    # Restoring the transport alone does not resume collection.
    assert service.entries[identifier].suspended.is_set()
    assert await service.resume(identifier)
    assert not service.entries[identifier].suspended.is_set()
    await service.aclose()
    assert not clock.sleepers


async def test_fatal_health_retires_registration_and_cannot_resume(tmp_path, observer_data, recording):
    registry, instances, _, _ = recording
    clock = Clock()
    service = ObserverService(cwd=tmp_path, handler_registry=registry, auto_health=False, sleep=clock.sleep)
    identifier = (await service.start(record_data(observer_data)))['registration_id']

    async def invalid_target():
        raise HandlerHealthError('session identity changed')

    instances[0].check = invalid_target
    row = (await service.health())['registrations'][0]
    assert row['state'] == 'paused' and row['retired'] == 'session identity changed'
    assert service.entries[identifier].registration.task.done()
    assert instances[0].state == 'closed'
    assert not await service.resume(identifier)
    await service.aclose()


async def test_aclose_cancels_incomplete_start_and_closes_initialized_handlers(tmp_path, observer_data, recording):
    registry, instances, _, _ = recording
    service = ObserverService(cwd=tmp_path, handler_registry=registry)
    original = service._handler
    entered, released = asyncio.Event(), asyncio.Event()

    def handler(definition):
        receiver = original(definition)

        async def start():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                released.set()

        receiver.start = start
        return receiver

    service._handler = handler
    starting = asyncio.create_task(service.start(record_data(observer_data)))
    await entered.wait()
    await service.aclose()
    with pytest.raises(asyncio.CancelledError):
        await starting
    assert released.is_set()
    assert instances[0].state == 'closed'
    assert not service.entries and not service._starts and not service._pending_starts


async def test_cancelled_aclose_still_awaits_cleanup(tmp_path, observer_data, recording):
    registry, instances, _, _ = recording
    service = ObserverService(cwd=tmp_path, handler_registry=registry, auto_health=False)
    await service.start(record_data(observer_data))
    closing, release = asyncio.Event(), asyncio.Event()
    original = instances[0].close

    async def close():
        closing.set()
        await release.wait()
        await original()

    instances[0].close = close
    task = asyncio.create_task(service.aclose())
    await closing.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not service.entries and instances[0].state == 'closed'
    await service.aclose()


async def test_repeating_deadlines_start_after_receivers_and_one_off_pause_still_skips(
    tmp_path, observer_data, recording,
):
    registry, instances, _, _ = recording
    clock = Clock()
    service = ObserverService(cwd=tmp_path, handler_registry=registry, auto_health=False,
                              clock=lambda: clock.mono, wall_clock=lambda: clock.wall, sleep=clock.sleep)
    data = record_data(observer_data)
    data['sources'] = [{'id': 'timer', 'type': 'scheduler', 'schedule': {'at': '1970-01-01T00:01:41Z'}}]
    data['detectors'] = [{'id': 'due', 'type': 'scheduler.due', 'source': 'timer'}]
    data['routes'][0].update(detector='due', event_types=['scheduler.due'])
    identifier = (await service.start(data))['registration_id']
    await until(lambda: clock.sleepers)
    service.entries[identifier].suspended.set()
    clock.advance(10)
    await until(lambda: service.entries[identifier].sources['timer'] == 'completed')
    assert not instances[0].deliveries
    service.entries[identifier].suspended.clear()
    clock.advance(100)
    await asyncio.sleep(0)
    assert not instances[0].deliveries
    await service.aclose()
