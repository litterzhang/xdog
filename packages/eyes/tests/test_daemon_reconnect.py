"""Transport recovery retains collection, cursors and original durable destinations."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from xdog.eyes.daemon import DaemonObservers
from xdog.eyes.loader import definition_data, parse
from xdog.eyes.local_control import daemon_location


@asynccontextmanager
async def receiver(tmp_path):
    from xdog.coding.acp.ingress import IngressConfig, IngressServer
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.sessions.inbox_store import InboxStore

    manager = SessionManager(tmp_path / 'sessions')
    session = manager.create_session(working_dir=tmp_path)
    with TemporaryDirectory(prefix='eyes-reconnect-') as short:
        config = IngressConfig(manager._dir, tmp_path / 'inbox', Path(short) / 'receiver.sock')
        servers = []
        async def start(*, different_store=False):
            from dataclasses import replace
            selected = replace(config, inbox_dir=tmp_path / 'other-inbox') if different_store else config
            server = await IngressServer.start(selected)
            servers.append(server)
            return server
        def records():
            return InboxStore(manager._dir, config.inbox_dir).records(session.session_id, tmp_path)
        await start()
        try:
            yield config, session, start, records, servers
        finally:
            for server in reversed(servers):
                await server.aclose()


def definition(tmp_path, observer_data, config, session):
    observer_data['sources'][0].update(root=str(tmp_path), include=['source.txt'], poll_interval_ms=10)
    observer_data['detectors'][0].update(debounce_ms=10, max_batch_delay_ms=20)
    observer_data['handlers'][0] = {
        'id': 'notify', 'type': 'acp', 'delivery': 'enqueue', 'session': session.session_id,
        'connection': {'transport': 'unix', 'path': str(config.address)}, 'instruction': 'Inspect changes',
    }
    return definition_data(parse(observer_data, base_dir=tmp_path))


async def wait_for(predicate, *, tick=None):
    async with asyncio.timeout(12):
        while not predicate():
            if tick is not None:
                await tick()
            await asyncio.sleep(.025)


async def status(observers, identifier):
    row, = await observers.store.rows('test')
    assert row['id'] == identifier
    return observers.snapshot(row)


async def test_live_receiver_restart_keeps_observation_and_collects_bounded_backlog(
    tmp_path, observer_data, isolated_daemon_directory,
):
    async with receiver(tmp_path) as (config, session, start, records, servers):
        source = tmp_path / 'source.txt'
        source.write_text('baseline')
        observers = DaemonObservers(daemon_location(isolated_daemon_directory))
        await observers.open()
        try:
            _, result = await observers.dispatch('register', {'owner': 'test',
                'definition': definition(tmp_path, observer_data, config, session)})
            identifier = result['registration_id']
            await wait_for(lambda: observers.active[identifier].sources.get('files') == 'watching')
            original = observers.active[identifier]
            source.write_text('first')
            await wait_for(lambda: len(records()) == 1)
            await wait_for(lambda: original.routes.get('review') == 'delivery_queued')
            await servers[-1].aclose()
            await observers.health()
            assert (await status(observers, identifier))['state'] == 'degraded'
            row, = await observers.store.rows('test')
            assert row['state'] == 'running' and not original.task.done()

            source.write_text('second')
            await wait_for(lambda: original.routes.get('review') == 'delivery_retry')
            row, = await observers.store.rows('test')
            pending = json.loads(row['checkpoint'])['pending']['review']['delivery']
            source.write_text('third')
            async with asyncio.timeout(5):
                while True:
                    row, = await observers.store.rows('test')
                    checkpoint = json.loads(row['checkpoint'])
                    if checkpoint['backlog']:
                        break
                    await asyncio.sleep(.025)
            assert checkpoint['pending']['review']['delivery'] == pending
            assert not checkpoint['pending']['review']['paused']
            assert not checkpoint['overflowed']

            await start()
            await wait_for(lambda: len(records()) == 3, tick=observers.health)
            await wait_for(lambda: original.routes.get('review') == 'delivery_queued', tick=observers.health)
            assert observers.active[identifier] is original
            assert (await status(observers, identifier))['state'] == 'watching'
            ids = [row.message.message_id for row in records()]
            assert ids[1] == pending['id'] and len(set(ids)) == 3
        finally:
            await observers.aclose()


async def test_changed_receiver_namespace_is_quarantined_without_sending_to_it(
    tmp_path, observer_data, isolated_daemon_directory,
):
    async with receiver(tmp_path) as (config, session, start, records, servers):
        source = tmp_path / 'source.txt'
        source.write_text('baseline')
        observers = DaemonObservers(daemon_location(isolated_daemon_directory))
        await observers.open()
        try:
            _, result = await observers.dispatch('register', {'owner': 'test',
                'definition': definition(tmp_path, observer_data, config, session)})
            identifier = result['registration_id']
            active = observers.active[identifier]
            await wait_for(lambda: active.sources.get('files') == 'watching')
            await servers[-1].aclose()
            await observers.health()
            source.write_text('must not be retargeted')
            await start(different_store=True)
            await wait_for(lambda: bool(active.error), tick=observers.health)
            assert (await status(observers, identifier))['state'] == 'needs-review'
            assert 'binding changed' in active.error
            assert records() == ()
            assert not list((tmp_path / 'other-inbox').rglob('*.json'))
        finally:
            await observers.aclose()


async def test_daemon_restart_reconnects_without_rebaselining_while_receiver_was_offline(
    tmp_path, observer_data, isolated_daemon_directory,
):
    async with receiver(tmp_path) as (config, session, start, records, servers):
        source = tmp_path / 'source.txt'
        source.write_text('baseline')
        location = daemon_location(isolated_daemon_directory)
        observers = DaemonObservers(location)
        await observers.open()
        try:
            _, result = await observers.dispatch('register', {'owner': 'test',
                'definition': definition(tmp_path, observer_data, config, session)})
            identifier = result['registration_id']
            await wait_for(lambda: observers.active[identifier].sources.get('files') == 'watching')
            await observers.aclose()
            await servers[-1].aclose()
            source.write_text('changed during receiver and daemon downtime')
            observers = DaemonObservers(location)
            await observers.open()
            await observers.recover()
            await wait_for(lambda: bool(observers.active[identifier].handler_errors))
            assert (await status(observers, identifier))['state'] == 'degraded'
            await start()
            await wait_for(lambda: len(records()) == 1, tick=observers.health)
            assert (await status(observers, identifier))['state'] == 'watching'
            assert records()[0].message.evidence[0]['type'] == 'file.modified'
        finally:
            await observers.aclose()


@pytest.mark.parametrize('action', ['pause', 'cancel'])
async def test_explicit_control_cancels_owned_reconnect_and_cannot_be_undone_by_late_health(
    tmp_path, observer_data, isolated_daemon_directory, action, monkeypatch,
):
    async with receiver(tmp_path) as (config, session, _, _, servers):
        (tmp_path / 'source.txt').write_text('baseline')
        observers = DaemonObservers(daemon_location(isolated_daemon_directory))
        await observers.open()
        try:
            _, result = await observers.dispatch('register', {'owner': 'test',
                'definition': definition(tmp_path, observer_data, config, session)})
            identifier = result['registration_id']
            active = observers.active[identifier]
            await wait_for(lambda: active.sources.get('files') == 'watching')
            await servers[-1].aclose()
            await observers.health()
            entered, cancelled = asyncio.Event(), asyncio.Event()
            async def reconnect():
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            monkeypatch.setattr(active.handlers['notify'], 'resume', reconnect)
            active.retries.clear()
            checking = asyncio.create_task(observers.health())
            await asyncio.wait_for(entered.wait(), 2)
            _, stopped = await asyncio.wait_for(observers.dispatch(action, {
                'owner': 'test', 'registration_id': identifier}), 2)
            await checking
            assert cancelled.is_set()
            assert stopped['state'] == ('paused' if action == 'pause' else 'cancelled')
            assert identifier not in observers.active
            await observers.health()
            await observers.recover()
            assert identifier not in observers.active
        finally:
            await observers.aclose()


async def test_inflight_retry_safe_handler_restarts_only_the_original_delivery_identity(
    tmp_path, observer_data, isolated_daemon_directory, monkeypatch,
):
    from xdog.eyes.events import DeliveryResult
    from xdog.eyes.handlers.contracts import HandlerSnapshot

    received, queued = asyncio.Event(), asyncio.Event()
    calls, effects, handlers = [], set(), []
    class Handler:
        async def start(self):
            pass
        def snapshot(self):
            return HandlerSnapshot('ready', {}, binding={'receiver': 'original'}, retry_safe=True)
        def validate(self, value):
            pass
        async def check(self):
            pass
        async def close(self):
            pass
        async def deliver(self, value):
            calls.append(value)
            effects.add(value.id)  # Durable receiver-side deduplication.
            if len(calls) == 1:
                received.set()
                await asyncio.Event().wait()  # Accepted, ACK lost when Eyes exits.
            queued.set()
            return DeliveryResult('queued')
    def factory(*args):
        handler = Handler()
        handlers.append(handler)
        return handler
    monkeypatch.setattr('xdog.eyes.handlers.registry.HandlerRegistry.create', factory)
    source = tmp_path / 'source.txt'
    source.write_text('baseline')
    observer_data['sources'][0].update(root=str(tmp_path), include=['source.txt'], poll_interval_ms=10)
    observer_data['detectors'][0].update(debounce_ms=10, max_batch_delay_ms=20)
    location = daemon_location(isolated_daemon_directory)
    observers = DaemonObservers(location)
    await observers.open()
    try:
        _, result = await observers.dispatch('register', {'owner': 'test',
            'definition': definition_data(parse(observer_data, base_dir=tmp_path))})
        identifier = result['registration_id']
        await wait_for(lambda: observers.active[identifier].sources.get('files') == 'watching')
        source.write_text('accepted once')
        await asyncio.wait_for(received.wait(), 3)
        await observers.aclose()
        observers = DaemonObservers(location)
        await observers.open()
        await observers.recover()
        await asyncio.wait_for(queued.wait(), 3)
        await wait_for(lambda: observers.active[identifier].routes.get('review') == 'delivery_queued')
        assert len(handlers) == 2 and len(calls) == 2 and len(effects) == 1
        assert calls[0] == calls[1]
        assert (await status(observers, identifier))['state'] == 'watching'
    finally:
        await observers.aclose()
