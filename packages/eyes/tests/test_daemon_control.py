"""Real Unix control/SQLite, strictly private test directories, no gateway."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from xdog.eyes._daemon_control import DaemonControl
from xdog.eyes.control import request
from xdog.eyes.loader import definition_data, parse
from xdog.eyes.local_control import daemon_location
from xdog.eyes.runtime import ObserverRuntime


@pytest.fixture
async def daemon(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix='eyes-test-') as directory:
        location = daemon_location(Path(directory))
        tasks = []
        runtime = None

        async def boot(**options):
            nonlocal runtime
            runtime = ObserverRuntime(location, **options)
            task = asyncio.create_task(runtime.serve())
            tasks.append(task)
            async with asyncio.timeout(5):
                while True:
                    try:
                        if (await request(location, 'hello', {}))['status'] == 'ok':
                            break
                    except (FileNotFoundError, ConnectionRefusedError):
                        pass
                    await asyncio.sleep(.01)
            return runtime

        async def client(owner='goal:a'):
            value = DaemonControl(owner=owner, directory=Path(directory))
            value.location = runtime.location
            return value

        await boot()
        try:
            yield boot, client, lambda: runtime, location
        finally:
            runtime.stop.set()
            await asyncio.gather(*tasks)


def definition(tmp_path, observer_data):
    value = json.loads(json.dumps(observer_data))
    value['sources'][0].update(root=str(tmp_path), include=['*.json'], poll_interval_ms=50)
    value['detectors'][0].update(debounce_ms=50, max_batch_delay_ms=100)
    value['handlers'] = [{'id': value['handlers'][0]['id'], 'type': 'events'}]
    return definition_data(parse(value, base_dir=tmp_path))


async def test_detach_owner_fences_ack_and_recovery_without_acp(daemon, tmp_path, observer_data, monkeypatch):
    boot, client, current, location = daemon
    # A protocol import/connection on this path is always a bug.
    def forbidden(*args, **kwargs):
        raise AssertionError('events delivery must not instantiate ACP')
    monkeypatch.setattr('xdog.eyes.handlers.registry.HandlerRegistry.create', forbidden)
    first = await client()
    data = definition(tmp_path, observer_data)
    result = await first.start(data, delivery='events', key='tasks')
    identifier = result['registration_id']
    assert result['state'] == 'watching'
    assert (await first.start(data, delivery='events', key='tasks'))['duplicate']
    other = await client('goal:b')
    with pytest.raises(ValueError, match='owner'):
        await other.cancel(identifier)
    assert await other.events() == []
    await first.aclose()  # source survives host retirement
    (tmp_path / 'task.json').write_text('{}')
    restored = await client()
    rows = await restored.events(wait=3)
    assert len(rows) == 1 and rows[0]['registration_id'] == identifier
    original = rows[0]
    with pytest.raises(ValueError, match='owner'):
        await other.ack(original['sequence'])
    assert await other.events(after=0) == []
    old = current()
    old.stop.set()
    while old._lease >= 0:
        await asyncio.sleep(.01)
    await boot()
    new = await client()
    repeated = await new.start(data, delivery='events', key='tasks')
    assert repeated['registration_id'] == identifier
    assert await new.events(wait=1) == [original]
    await new.ack(original['sequence'])
    await new.ack(original['sequence'])
    assert await new.events() == []
    assert await new.events(after=0) == [original]
    await new.cancel(identifier)
    assert (await new.cancel(identifier))['state'] == 'cancelled'
    with pytest.raises(ValueError, match='cannot be resumed'):
        await new.resume(identifier)


async def test_pause_is_explicit_duplicate_start_never_releases_pause(daemon, tmp_path, observer_data):
    _, client, _, _ = daemon
    value = await client()
    data = definition(tmp_path, observer_data)
    result = await value.start(data, delivery='events', key='tasks')
    identifier = result['registration_id']
    assert (await value.pause(identifier))['state'] == 'paused'
    assert (await value.pause(identifier))['state'] == 'paused'
    await value.aclose()
    value = await client()
    paused = await value.start(data, delivery='events', key='tasks', wait_ready=False)
    assert paused['state'] == 'paused' and paused['registration_id'] == identifier
    assert (await value.resume(identifier))['registration_id'] == identifier
    assert (await value.resume(identifier))['registration_id'] == identifier
    await value.wait_ready(identifier)
    data['name'] += '-conflict'
    with pytest.raises(ValueError, match='different content'):
        await value.start(data, delivery='events', key='tasks')


async def test_cursor_survives_restart_and_detects_changes_during_downtime(daemon, tmp_path, observer_data):
    boot, client, current, _ = daemon
    (tmp_path / 'task.json').write_text('{"n":1}')
    value = await client()
    result = await value.start(definition(tmp_path, observer_data), delivery='events')
    old = current()
    old.stop.set()
    while old._lease >= 0:
        await asyncio.sleep(.01)
    (tmp_path / 'task.json').write_text('{"n":2}')
    await boot()
    new = await client()
    rows = await new.events(wait=3)
    assert rows[0]['registration_id'] == result['registration_id']
    assert rows[0]['delivery']['events'][0]['type'] == 'file.modified'
    assert rows[0]['delivery']['events'][0]['evidence'][0]['before'] is not None
    await new.ack(rows[0]['sequence'])


async def test_subscribe_refreshes_generation_without_retrying_mutators(daemon, tmp_path, observer_data, monkeypatch):
    boot, client, current, _ = daemon
    value = await client()
    result = await value.start(definition(tmp_path, observer_data), delivery='events')
    subscription = value.subscribe()
    (tmp_path / 'first.json').write_text('1')
    first = await asyncio.wait_for(anext(subscription), 3)
    await value.ack(first['sequence'])
    old = current()
    old.stop.set()
    while old._lease >= 0:
        await asyncio.sleep(.01)
    await boot()
    # The existing client intentionally retains the old generation. Reconnection
    # must not register, resume or mutate its original observation.
    original = value._request
    calls = []

    async def record(action, body, **kwargs):
        calls.append(action)
        return await original(action, body, **kwargs)

    monkeypatch.setattr(value, '_request', record)
    (tmp_path / 'second.json').write_text('2')
    second = await asyncio.wait_for(anext(subscription), 5)
    assert second['sequence'] > first['sequence']
    assert second['registration_id'] == result['registration_id']
    assert set(calls) == {'events'}  # read-only owner refresh, never daemon start
    await subscription.aclose()


async def test_unknown_external_send_is_not_replayed_on_resume_or_restart(
    daemon, tmp_path, observer_data, monkeypatch,
):
    from xdog.eyes.handlers.contracts import HandlerSnapshot

    boot, client, current, _ = daemon
    delivered = asyncio.Event()
    calls = []

    class Handler:
        async def start(self):
            pass

        def validate(self, value):
            pass

        async def deliver(self, value):
            calls.append(value.id)
            delivered.set()
            await asyncio.Event().wait()  # unknown outcome when daemon shuts down

        def snapshot(self):
            return HandlerSnapshot('ready', {'inbox_id': 'original'})

        async def check(self):
            pass

        async def close(self):
            pass

    monkeypatch.setattr('xdog.eyes.handlers.registry.HandlerRegistry.create', lambda *args: Handler())
    value = await client()
    data = definition(tmp_path, observer_data)
    observer_data['handlers'][0]['delivery'] = 'enqueue'
    data['handlers'] = definition_data(parse(observer_data, base_dir=tmp_path))['handlers']
    result = await value.start(data, delivery='handlers')
    (tmp_path / 'task.json').write_text('{}')
    await asyncio.wait_for(delivered.wait(), 3)
    old = current()
    old.stop.set()
    while old._lease >= 0:
        await asyncio.sleep(.01)
    await boot()
    restored = await client()
    assert (await restored.status(result['registration_id']))['state'] == 'needs-review'
    duplicate = await restored.start(data, delivery='handlers', wait_ready=False)
    assert duplicate['registration_id'] == result['registration_id']
    with pytest.raises(ValueError, match='cannot be resumed'):
        await restored.resume(result['registration_id'])
    await asyncio.sleep(.2)
    assert len(calls) == 1


async def test_shared_process_cli_and_clients_have_same_inventory(
    isolated_daemon_directory, tmp_path, observer_data,
):
    import subprocess
    import sys

    first, second = await asyncio.gather(
        DaemonControl.open(owner='shared'), DaemonControl.open(owner='shared'))
    try:
        assert first.location.generation == second.location.generation
        result = await first.start(definition(tmp_path, observer_data), delivery='events')
        other_cwd = tmp_path / 'different-cli-cwd'
        other_cwd.mkdir()
        output = await asyncio.to_thread(subprocess.run,
            [sys.executable, '-m', 'xdog.eyes', '--owner', 'shared',
             'status', result['registration_id']], capture_output=True, text=True, timeout=10, cwd=other_cwd)
        assert output.returncode == 0, output.stderr
        assert json.loads(output.stdout)['registration_id'] == result['registration_id']
        assert (await second.status(result['registration_id']))['state'] == 'watching'
        await first.aclose()
        assert (await second.daemon_status())['state'] == 'running'
    finally:
        await second.stop_daemon()
        await second.aclose()


async def test_register_ack_loss_does_not_cancel_accepted_owner_registration(daemon, tmp_path, observer_data):
    from xdog.eyes.local_control import encode_frame

    _, client, current, _ = daemon
    value = await client()
    data = definition(tmp_path, observer_data)
    _, writer = await asyncio.open_unix_connection(current().location.socket)
    writer.write(encode_frame({'version': 1, 'generation': current().location.generation,
        'action': 'register', 'body': {'owner': value.owner,
            'key': 'tasks', 'definition': data, 'delivery': 'events', 'recover': True}}))
    await writer.drain()
    writer.close()  # lose ACK before admission/scan; not cancellation authority
    await writer.wait_closed()
    async with asyncio.timeout(3):
        while not (await value.status())['registrations']:
            await asyncio.sleep(.01)
    previous = (await value.status())['registrations'][0]
    duplicate = await value.start(data, delivery='events', key='tasks')
    assert duplicate['registration_id'] == previous['registration_id']
    assert duplicate['duplicate']
    assert len((await value.status())['registrations']) == 1
    same_owner = await client()
    assert (await same_owner.pause(duplicate['registration_id']))['state'] == 'paused'


async def test_runtime_clock_injection_and_explicit_pause_skip_deadlines(daemon, tmp_path, observer_data):
    from datetime import UTC, datetime

    from test_support.eyes_clock import Clock

    boot, client, current, _ = daemon
    old = current()
    old.stop.set()
    while old._lease >= 0:
        await asyncio.sleep(.01)
    clock = Clock(wall=100.)  # deliberately nowhere near the real wall clock
    await boot(clock=lambda: clock.mono, wall_clock=lambda: clock.wall, sleep=clock.sleep)
    value = await client()
    data = definition(tmp_path, observer_data)
    data['sources'] = [{'id': 'timer', 'type': 'scheduler', 'schedule': {
        'at': datetime.fromtimestamp(110., UTC).isoformat()}}]
    data['detectors'] = [{'id': 'due', 'type': 'scheduler.due', 'source': 'timer'}]
    data['routes'][0].update(detector='due', event_types=['scheduler.due'])
    result = await value.start(parse(data, base_dir=tmp_path), delivery='events')
    await value.pause(result['registration_id'])
    clock.advance(20)
    await value.resume(result['registration_id'])
    clock.advance(1)
    await asyncio.sleep(.1)
    assert await value.events() == []  # expired explicitly paused one-off is skipped
    data['name'] = 'second'
    data['sources'][0]['schedule']['at'] = datetime.fromtimestamp(clock.wall + 5, UTC).isoformat()
    await value.start(data, delivery='events')
    clock.advance(10)
    # Drive only injected sleepers; real Unix/SQLite IO still uses actual workers.
    async with asyncio.timeout(3):
        while not (rows := await value.events()):
            clock.advance(.1)
            await asyncio.sleep(.01)
    assert rows[0]['delivery']['events'][0]['evidence'][0]['scheduled_at'] == 126.


async def test_empty_response_is_offline_but_malformed_response_is_not_retried(daemon, monkeypatch):
    from xdog.eyes.local_control import ControlError

    _, _, current, _ = daemon
    frame = b''

    class Reader:
        async def readline(self):
            return frame

    class Writer:
        def write(self, value):
            pass

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def connect(*args, **kwargs):
        return Reader(), Writer()

    with monkeypatch.context() as patch:
        patch.setattr('xdog.eyes.control.asyncio.open_unix_connection', connect)
        with pytest.raises(ConnectionError, match='closed the control connection'):
            await request(current().location, 'hello', {})
        frame = b'invalid-json\n'
        with pytest.raises(ControlError, match='Invalid Eyes control JSON'):
            await request(current().location, 'hello', {})


async def test_changed_original_handler_binding_cannot_acquire_a_saved_observer(
    daemon, tmp_path, observer_data, monkeypatch,
):
    from xdog.eyes.handlers.contracts import HandlerSnapshot

    _, client, _, _ = daemon
    scope = ['original-inbox']
    sends = []

    class Handler:
        def __init__(self):
            self.inbox = scope[0]

        async def start(self):
            pass

        def validate(self, value):
            pass

        async def deliver(self, value):
            sends.append(value.id)
            raise AssertionError('Retargeted handlers must not send anything')

        def snapshot(self):
            return HandlerSnapshot('ready', {'inbox_id': self.inbox, 'branch_id': 'original-branch'})

        async def close(self):
            pass

        async def check(self):
            pass

    monkeypatch.setattr('xdog.eyes.handlers.registry.HandlerRegistry.create', lambda *args: Handler())
    value = await client()
    data = definition(tmp_path, observer_data)
    observer_data['handlers'][0]['delivery'] = 'enqueue'
    data['handlers'] = definition_data(parse(observer_data, base_dir=tmp_path))['handlers']
    result = await value.start(data, delivery='handlers')
    await value.pause(result['registration_id'])
    scope[0] = 'replacement-inbox'
    with pytest.raises(ValueError, match='binding changed'):
        await value.resume(result['registration_id'])
        await value.wait_ready(result['registration_id'], timeout=3)
    assert (await value.status(result['registration_id']))['state'] == 'needs-review'
    assert sends == []


@pytest.mark.parametrize('version', [0, 1, 3])
async def test_incompatible_journal_is_rejected_without_migration_or_replay(tmp_path, monkeypatch, version):
    import os
    import sqlite3

    from xdog.eyes.daemon import DaemonObservers

    location = daemon_location(tmp_path / 'private')
    path = location.directory / 'daemon.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE registrations (owner TEXT, workspace TEXT, content TEXT, state TEXT)')
        db.execute('INSERT INTO registrations VALUES (?,?,?,?)', ('legacy', '/old', '{}', 'running'))
        db.execute(f'PRAGMA user_version={version}')
    path.chmod(0o600)
    original = path.read_bytes()

    async def forbidden(*args):
        raise AssertionError('Incompatible storage must never activate registrations')

    monkeypatch.setattr(DaemonObservers, 'activate', forbidden)
    observers = DaemonObservers(location)
    with pytest.raises(ValueError, match=f'Incompatible Eyes daemon journal version {version}'):
        await observers.open()
    assert not observers.active
    assert path.read_bytes() == original
    assert os.stat(path).st_mode & 0o777 == 0o600


async def test_daemon_rejects_relative_source_and_retired_workspace_field(daemon, tmp_path, observer_data):
    _, client, current, _ = daemon
    value = await client()
    data = definition(tmp_path, observer_data)
    data['sources'][0]['root'] = '.'
    with pytest.raises(ValueError, match='normalized definition'):
        await value.start(data, delivery='events', wait_ready=False)
    response = await request(current().location, 'owner-status', {'owner': value.owner, 'workspace': str(tmp_path)})
    assert response['status'] not in {'ok', 'accepted', 'duplicate'}
    assert (await value.status())['registrations'] == []


@pytest.mark.parametrize('action', ['pause', 'cancel'])
async def test_slow_activation_and_restart_do_not_block_control_and_are_fenced(
    daemon, tmp_path, observer_data, monkeypatch, action,
):
    from xdog.eyes.handlers.contracts import HandlerSnapshot
    from xdog.eyes.process import ensure_runtime

    boot, client, current, location = daemon
    starts, closes, sends = [], [], []
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowHandler:
        async def start(self):
            starts.append(self)
            started.set()
            await release.wait()  # slower than both the 8s bootstrap and ACP 10s connect

        async def close(self):
            closes.append(self)

        def snapshot(self):
            return HandlerSnapshot('ready', {'inbox': 'original'})

        async def check(self):
            raise AssertionError('Health must not run on a half-started receiver')

        def validate(self, value):
            pass

        async def deliver(self, value):
            sends.append(value)
            raise AssertionError('A fenced activation may never deliver')

    monkeypatch.setattr('xdog.eyes.handlers.registry.HandlerRegistry.create', lambda *args: SlowHandler())
    data = definition(tmp_path, observer_data)
    data['handlers'] = definition_data(parse(observer_data, base_dir=tmp_path))['handlers']
    value = await client()
    async with asyncio.timeout(1):
        accepted = await value.start(data, wait_ready=False)
        await started.wait()
        assert (await value.status(accepted['registration_id']))['state'] == 'baselining'
    old = current()
    old.stop.set()
    async with asyncio.timeout(1):
        while old._lease >= 0:
            await asyncio.sleep(.01)
    started.clear()
    async with asyncio.timeout(1):
        await boot()
        await started.wait()
        # The actual startup probe can reuse the live control plane while every
        # restored receiver is still blocked forever.
        ensured = await asyncio.to_thread(ensure_runtime, location, timeout=.5)
        assert ensured.generation == current().location.generation
        restored = await client()
        active = current().daemon.active[accepted['registration_id']]
        assert (await getattr(restored, action)(accepted['registration_id']))['state'] == (
            'paused' if action == 'pause' else 'cancelled')
    assert active.task.done()
    assert len(starts) == len(closes) == 2
    release.set()
    (tmp_path / 'must-not-send.json').write_text('{}')
    await asyncio.sleep(.1)
    assert sends == []
    # Neither duplicate admission nor another restart releases the original fence.
    duplicate = await restored.start(data, wait_ready=False)
    assert duplicate['registration_id'] == accepted['registration_id']
    assert duplicate['state'] == ('paused' if action == 'pause' else 'cancelled')
    old = current()
    old.stop.set()
    while old._lease >= 0:
        await asyncio.sleep(.01)
    await boot()
    assert len(starts) == 2
    assert accepted['registration_id'] not in current().daemon.active


@pytest.mark.parametrize('body', [
    {},  # old wire-v1 hello: generation alone was never feature negotiation
    {'runtime_type': 'eyes-observer', 'control_protocol': 1, 'feature_version': 1, 'features': []},
    {'runtime_type': 'eyes-daemon', 'control_protocol': 2, 'feature_version': 1, 'features': []},
    {'runtime_type': 'eyes-daemon', 'control_protocol': True, 'feature_version': 1, 'features': []},
    {'runtime_type': 'eyes-daemon', 'control_protocol': 1, 'feature_version': 2, 'features': []},
    {'runtime_type': 'eyes-daemon', 'control_protocol': 1, 'feature_version': 1, 'features': []},
])
async def test_live_legacy_or_incompatible_daemon_is_not_reused_stopped_or_replaced(daemon, monkeypatch, body):
    from xdog.eyes.process import ensure_runtime

    _, client, current, location = daemon
    original = current().dispatch
    actions = []

    async def dispatch(frame):
        actions.append(frame['action'])
        if frame['action'] == 'hello':
            return 'ok', body
        return await original(frame)

    def forbidden(*args, **kwargs):
        raise AssertionError('Never spawn over a live incompatible daemon')

    monkeypatch.setattr(current(), 'dispatch', dispatch)
    monkeypatch.setattr('xdog.eyes.process.subprocess.Popen', forbidden)
    before = (location.directory / 'daemon.sqlite3').read_bytes()
    with pytest.raises(RuntimeError, match='Incompatible live Eyes runtime'):
        await asyncio.to_thread(ensure_runtime, location, timeout=.5)
    value = await client()
    with pytest.raises(RuntimeError, match='Incompatible live Eyes runtime'):
        await value.cancel('anything')
    assert set(actions) == {'hello'}
    assert not current().stop.is_set()
    assert (location.directory / 'daemon.sqlite3').read_bytes() == before
    assert (await request(location, 'hello', {}))['status'] == 'ok'


async def test_recovery_reads_rows_under_admission_fence(daemon, tmp_path, observer_data):
    _, client, current, _ = daemon
    value = await client()
    result = await value.start(definition(tmp_path, observer_data), delivery='events')
    await value.pause(result['registration_id'])
    # Model a recovery task delayed until after the control plane has accepted a
    # pause. Recovery must read current durable state rather than a boot-time list.
    await current().daemon.recover()
    assert result['registration_id'] not in current().daemon.active
    assert (await value.status(result['registration_id']))['state'] == 'paused'


async def test_real_process_bootstrap_does_not_wait_for_ten_second_acp_initialize(tmp_path, observer_data):
    import sys

    value = await DaemonControl.open(owner='slow-peer')
    data = definition(tmp_path, observer_data)
    observer_data['handlers'][0]['connection'] = {
        'transport': 'stdio', 'command': [sys.executable, '-c', 'import time; time.sleep(60)']}
    data['handlers'] = definition_data(parse(observer_data, base_dir=tmp_path))['handlers']
    try:
        async with asyncio.timeout(3):
            accepted = await value.start(data, wait_ready=False)
        await value.stop_daemon()
        # Default ensure_runtime bootstrap is 8s, while the saved peer never
        # answers initialize (ACP times out after 10s). Recovery must be separate.
        async with asyncio.timeout(5):
            await value.start_daemon()
            assert (await value.status(accepted['registration_id']))['state'] == 'baselining'
            assert (await value.pause(accepted['registration_id']))['state'] == 'paused'
        assert len((await value.status())['registrations']) == 1
        assert (await value.daemon_status())['state'] == 'running'
    finally:
        await value.aclose()


async def test_late_health_failure_cannot_override_pause_or_cancel(daemon, tmp_path, observer_data, monkeypatch):
    from xdog.eyes.handlers.contracts import HandlerSnapshot

    _, client, current, _ = daemon
    checking = asyncio.Event()
    release = asyncio.Event()

    class Handler:
        async def start(self):
            pass

        def snapshot(self):
            return HandlerSnapshot('ready', {})

        async def close(self):
            pass

        async def check(self):
            checking.set()
            await release.wait()
            raise RuntimeError('late health failure for retired owner')

        def validate(self, value):
            pass

        async def deliver(self, value):
            raise AssertionError('No delivery expected')

    monkeypatch.setattr('xdog.eyes.handlers.registry.HandlerRegistry.create', lambda *args: Handler())
    value = await client()
    data = definition(tmp_path, observer_data)
    data['handlers'] = definition_data(parse(observer_data, base_dir=tmp_path))['handlers']
    result = await value.start(data)
    await asyncio.wait_for(checking.wait(), 2)
    async with asyncio.timeout(.5):
        assert (await value.cancel(result['registration_id']))['state'] == 'cancelled'
    release.set()
    await asyncio.sleep(.1)
    assert (await value.status(result['registration_id']))['state'] == 'cancelled'


async def test_slow_saved_receiver_does_not_block_independent_saved_registration(
    daemon, tmp_path, observer_data, monkeypatch,
):
    from xdog.eyes.handlers.contracts import HandlerSnapshot

    boot, client, current, _ = daemon
    connecting = asyncio.Event()

    class Slow:
        async def start(self):
            connecting.set()
            await asyncio.Event().wait()

        def snapshot(self):
            return HandlerSnapshot('ready', {})

        async def close(self):
            pass

    monkeypatch.setattr('xdog.eyes.handlers.registry.HandlerRegistry.create', lambda *args: Slow())
    value = await client()
    data = definition(tmp_path, observer_data)
    data['handlers'] = definition_data(parse(observer_data, base_dir=tmp_path))['handlers']
    slow = await value.start(data, key='first-slow', wait_ready=False)
    fast = await value.start(definition(tmp_path, observer_data), key='second-independent', delivery='events')
    old = current()
    old.stop.set()
    while old._lease >= 0:
        await asyncio.sleep(.01)
    connecting.clear()
    await boot()
    await asyncio.wait_for(connecting.wait(), 1)
    restored = await client()
    await restored.wait_ready(fast['registration_id'], timeout=1)
    assert (await restored.status(slow['registration_id']))['state'] == 'baselining'
    await restored.cancel(slow['registration_id'])
