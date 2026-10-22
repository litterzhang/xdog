"""Runtime admission, control and health are independent of coding's implementation."""

import asyncio
from copy import deepcopy

import pytest

from packages.eyes.tests.test_acp_unix import unix_peer as sdk_peer

unix_peer = sdk_peer


def unix_definition(data,path,root):
    data = deepcopy(data)
    data['sources'][0].update(root=str(root),include=['src/**'],poll_interval_ms=50)
    data['detectors'][0].update(debounce_ms=50,max_batch_delay_ms=100)
    data['handlers'][0].update(connection={'transport':'unix','path':str(path)},session='target')
    return data


async def wait_for(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_all_handlers_must_connect_before_any_source_scan(tmp_path,observer_data,unix_peer):
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.runtime import ObserverRuntime
    from xdog.eyes.sources.file import FileSnapshot

    path,events,gate,writers = unix_peer
    scans = []

    async def scan(source):
        scans.append(source.id)
        return FileSnapshot(())

    data = unix_definition(observer_data,path,tmp_path)
    data['handlers'].append({**deepcopy(data['handlers'][0]),'id':'bad','session':'missing'})
    runtime = ObserverRuntime(runtime_location(tmp_path),scanner=scan)
    with pytest.raises(Exception):
        await runtime.start(data)
    assert not scans and not runtime.entries and not runtime.names
    await wait_for(lambda:not writers)
    assert not [e for e in events if e[0]=='prompt']


async def test_same_definition_is_idempotent_and_changes_require_stop(tmp_path,observer_data,unix_peer):
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.runtime import ObserverRuntime

    path,events,gate,writers = unix_peer
    (tmp_path/'src').mkdir()
    runtime = ObserverRuntime(runtime_location(tmp_path))
    data = unix_definition(observer_data,path,tmp_path)
    try:
        first,second = await asyncio.gather(runtime.start(data),runtime.start(deepcopy(data)))
        assert first['registration_id']==second['registration_id']
        assert len([e for e in events if e[0]=='initialize'])==1
        changed = deepcopy(data)
        changed['handlers'][0]['instruction']='Another instruction'
        with pytest.raises(ValueError,match='stop'):
            await runtime.start(changed)
        assert len(runtime.entries)==1
    finally:
        for identifier in tuple(runtime.entries):
            await runtime.stop_entry(identifier)


async def test_one_route_success_does_not_clear_another_route_unknown_pause(tmp_path,observer_data,unix_peer):
    from xdog.eyes.events import RuntimeEvent
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.runtime import ObserverRuntime

    path,events,gate,writers = unix_peer
    (tmp_path/'src').mkdir()
    data = unix_definition(observer_data,path,tmp_path)
    data['routes'].append({**data['routes'][0],'id':'other'})
    runtime = ObserverRuntime(runtime_location(tmp_path))
    identifier = (await runtime.start(data))['registration_id']
    try:
        runtime.emit(identifier,RuntimeEvent('delivery_outcome_unknown',identifier,route_id='review',
                                            delivery_id='uncertain',error='lost response'))
        runtime.emit(identifier,RuntimeEvent('paused',identifier,route_id='review',delivery_id='uncertain',error='lost'))
        runtime.emit(identifier,RuntimeEvent('delivery_succeeded',identifier,route_id='other',delivery_id='success'))
        row = (await runtime.status(identifier))['registrations'][0]
        assert row['state']=='paused'
        assert row['routes']['review']['state']=='outcome-unknown'
        assert row['routes']['other']['state']=='succeeded'
        assert not await runtime.resume_entry(identifier)
    finally:
        await runtime.stop_entry(identifier)


async def test_disconnected_prompt_peer_freezes_collection_without_reconnecting(tmp_path,observer_data):
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.runtime import ObserverRuntime

    from packages.eyes.tests.test_acp_handler import definition, rows

    peer = definition(tmp_path/'record',mode='disconnect')
    (tmp_path/'src').mkdir()
    data = deepcopy(observer_data)
    data['sources'][0].update(root=str(tmp_path),include=['src/**'],poll_interval_ms=50)
    data['detectors'][0].update(debounce_ms=50,max_batch_delay_ms=100)
    data['handlers'][0].update(connection={'transport':'stdio','command':list(peer.connection.command)},session='target')
    runtime = ObserverRuntime(runtime_location(tmp_path))
    identifier = (await runtime.start(data))['registration_id']
    try:
        await wait_for(lambda:runtime.entries[identifier].sources.get('files')=='watching')
        (tmp_path/'src'/'a').write_text('change')
        async with asyncio.timeout(5):
            while True:
                row = (await runtime.status(identifier))['registrations'][0]
                if row['state']=='paused':
                    break
                await asyncio.sleep(0.02)
        assert row['routes']['review']['state']=='outcome-unknown'
        assert not await runtime.resume_entry(identifier)
        assert len([r for r in rows(tmp_path/'record') if r['kind']=='initialize'])==1
    finally:
        await runtime.stop_entry(identifier)


async def test_cli_uses_unix_acp_without_coding_cli(tmp_path,observer_data,unix_peer,monkeypatch,capsys):
    import json

    from xdog.eyes import process
    from xdog.eyes.cli import main
    from xdog.eyes.local_control import daemon_location

    path,events,gate,writers = unix_peer
    (tmp_path/'src').mkdir()
    data = unix_definition(observer_data,path,tmp_path)
    definition = tmp_path/'eyes.json'
    definition.write_text(json.dumps(data))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PATH','/no-coding-executable')
    identifier = None
    child = None
    try:
        assert await asyncio.to_thread(main,['start',str(definition)])==0
        identifier = json.loads(capsys.readouterr().out)['registration_id']
        child = process._children[str(daemon_location().directory)]
        assert await asyncio.to_thread(main,['start',str(definition)])==0
        assert json.loads(capsys.readouterr().out)['registration_id']==identifier
        async with asyncio.timeout(5):
            while True:
                assert await asyncio.to_thread(main,['status',identifier])==0
                row = json.loads(capsys.readouterr().out)
                if row['sources']['files']=='watching':
                    break
                await asyncio.sleep(0.02)
        (tmp_path/'src'/'a.py').write_text('changed')
        await wait_for(lambda:any(kind=='prompt' for kind,value in events))
        assert await asyncio.to_thread(main,['stop',identifier])==0
        assert json.loads(capsys.readouterr().out)['state'] == 'cancelled'
        assert child.poll() is None  # last cancel does not stop a shared daemon
        assert await asyncio.to_thread(main,['daemon','stop']) == 0
        assert await asyncio.to_thread(child.wait,timeout=5)==0
        assert not daemon_location().socket.exists()
    finally:
        await asyncio.to_thread(main,['daemon','stop'])
        if child is not None and child.poll() is None:
            await asyncio.to_thread(child.wait,timeout=5)


async def test_concurrent_starters_share_runtime_generation_and_registration(tmp_path,observer_data,unix_peer):
    from xdog.eyes.control import request
    from xdog.eyes.local_control import object_value, runtime_location
    from xdog.eyes.process import ensure_runtime, reap_runtime

    path,events,gate,writers = unix_peer
    (tmp_path/'src').mkdir()
    data = unix_definition(observer_data,path,tmp_path)
    location = runtime_location(tmp_path)

    def start_one():
        current = ensure_runtime(location)
        return current,asyncio.run(request(current,'start',{'definition':object_value(data)}))

    results = await asyncio.gather(*(asyncio.to_thread(start_one) for _ in range(4)))
    current = results[0][0]
    identifiers = {row[1]['body']['registration_id'] for row in results}
    try:
        assert len(identifiers)==1 and len({row[0].generation for row in results})==1
        assert len([e for e in events if e[0]=='initialize'])==1
    finally:
        for identifier in identifiers:
            await request(current,'stop',{'registration_id':identifier})
        assert await asyncio.to_thread(reap_runtime,current,timeout=5)


async def test_runtime_shutdown_settles_a_direct_pending_start(tmp_path,observer_data,unix_peer):
    from xdog.eyes.control import request
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.runtime import ObserverRuntime

    path,events,gate,writers = unix_peer
    (tmp_path/'src').mkdir()
    runtime = ObserverRuntime(runtime_location(tmp_path))
    serving = asyncio.create_task(runtime.serve())
    starting = None
    try:
        async with asyncio.timeout(3):
            while True:
                try:
                    await request(runtime.location,'hello',{})
                    break
                except OSError:
                    await asyncio.sleep(0.01)
        gate.clear()
        starting = asyncio.create_task(runtime.start(unix_definition(observer_data,path,tmp_path)))
        await wait_for(lambda:any(e[0]=='initialize' for e in events))
        runtime.stop.set()
        await asyncio.wait_for(serving,3)
        assert starting.done(), 'startup target connections are owned by the runtime too'
        assert not runtime.entries
    finally:
        gate.set()
        runtime.stop.set()
        if starting is not None:
            starting.cancel()
            await asyncio.gather(starting,return_exceptions=True)
        await asyncio.gather(serving,return_exceptions=True)


async def test_crashed_daemon_status_is_readonly_and_explicit_start_restores_original_cursor(
    tmp_path,observer_data,unix_peer,monkeypatch,capsys
):
    import json

    from xdog.eyes import process
    from xdog.eyes.cli import main
    from xdog.eyes.local_control import daemon_location

    path,events,gate,writers = unix_peer
    (tmp_path/'src').mkdir()
    changed = tmp_path/'src'/'a'
    changed.write_text('baseline')
    document = tmp_path/'eyes.json'
    document.write_text(json.dumps(unix_definition(observer_data,path,tmp_path)))
    monkeypatch.chdir(tmp_path)
    assert await asyncio.to_thread(main,['start',str(document)])==0
    first = json.loads(capsys.readouterr().out)['registration_id']
    location = daemon_location()
    child = process._children[str(location.directory)]
    child.kill()
    await asyncio.to_thread(child.wait,timeout=5)
    changed.write_text('changed while no observer exists')
    assert await asyncio.to_thread(main,['status'])==0
    capsys.readouterr()
    assert len([e for e in events if e[0]=='initialize'])==1
    assert await asyncio.to_thread(main,['start',str(document)])==0
    second = json.loads(capsys.readouterr().out)['registration_id']
    assert first==second
    new_child = process._children[str(location.directory)]
    try:
        async with asyncio.timeout(5):
            while True:
                await asyncio.to_thread(main,['status',second])
                row = json.loads(capsys.readouterr().out)
                if row['sources']['files']=='watching':
                    break
                await asyncio.sleep(0.01)
        await wait_for(lambda:any(e[0]=='prompt' for e in events))
    finally:
        await asyncio.to_thread(main,['daemon','stop'])
        await asyncio.to_thread(new_child.wait,timeout=5)


async def test_runtime_canonical_socket_is_absent_until_private_listener_is_ready(tmp_path, monkeypatch):
    import os
    import stat

    from xdog.eyes import runtime as module
    from xdog.eyes.control import request
    from xdog.eyes.local_control import read_location, runtime_location, validate_socket

    runtime = module.ObserverRuntime(runtime_location(tmp_path))
    entered, release = asyncio.Event(), asyncio.Event()
    original = asyncio.start_unix_server
    staged = []

    async def held_server(*args, **kwargs):
        server = await original(*args, **kwargs)
        # Hold the exact original bind-to-chmod gap. Force a permissive initial
        # inode regardless of the test runner's umask, without changing the umask.
        if kwargs.get('path') == str(runtime.location.socket):
            os.chmod(runtime.location.socket, 0o666)
        else:
            staged.append(kwargs['sock'].getsockname())
        entered.set()
        try:
            await release.wait()
            return server
        except BaseException:
            server.close()
            await server.wait_closed()
            raise

    monkeypatch.setattr(module.asyncio, 'start_unix_server', held_server)
    serving = asyncio.create_task(runtime.serve())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        # Startup absence is transient; an exposed unsafe inode is NOT and must
        # still fail closed as ControlError, reproducing the full-gate regression.
        with pytest.raises(FileNotFoundError):
            await request(runtime.location, 'hello', {})
        assert not runtime.location.socket.exists()
        assert len(staged) == 1
        info = os.lstat(staged[0])
        assert stat.S_ISSOCK(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_uid == os.getuid()
        release.set()
        await wait_for(runtime.location.socket.exists)
        validate_socket(runtime.location)
        assert read_location(runtime.location).generation == runtime.location.generation
        assert (await request(runtime.location, 'hello', {}))['status'] == 'ok'
    finally:
        release.set()
        runtime.stop.set()
        await asyncio.wait_for(serving, 3)
    assert not runtime.location.socket.exists()
    assert not (runtime.location.directory / 'owner.json').exists()
    assert not [path for path in runtime.location.directory.iterdir() if path.name.startswith('.eyes-')]


async def test_runtime_socket_publication_refuses_a_new_canonical_occupant(tmp_path, monkeypatch):
    from xdog.eyes import runtime as module
    from xdog.eyes.local_control import ControlError, runtime_location, validate_socket

    runtime = module.ObserverRuntime(runtime_location(tmp_path))
    original = module.publish_location
    identity = []

    def occupied(location):
        assert not location.socket.exists()
        location.socket.write_text('preserve the new occupant')
        location.socket.chmod(0o600)
        info = location.socket.lstat()
        identity.append((info.st_dev, info.st_ino))
        return original(location)

    monkeypatch.setattr(module, 'publish_location', occupied)
    try:
        with pytest.raises(FileExistsError):
            await runtime.serve()
        assert runtime.location.socket.read_text() == 'preserve the new occupant'
        info = runtime.location.socket.lstat()
        assert (info.st_dev, info.st_ino) == identity[0]
        with pytest.raises(ControlError, match='private owned Unix socket'):
            validate_socket(runtime.location)
        assert runtime._lease == -1 and not (runtime.location.directory / 'owner.json').exists()
        assert not [path for path in runtime.location.directory.iterdir() if path.name.startswith('.eyes-')]
    finally:
        runtime.location.socket.unlink(missing_ok=True)


async def test_runtime_private_socket_setup_failure_never_publishes_or_leaks_staging(tmp_path, monkeypatch):
    from pathlib import Path

    from xdog.eyes import runtime as module
    from xdog.eyes.local_control import runtime_location

    runtime = module.ObserverRuntime(runtime_location(tmp_path))
    original = module.os.chmod
    attempted = []

    def refused(path, mode):
        if Path(path).name.startswith('.eyes-'):
            attempted.append(path)
            raise PermissionError('private socket setup refused')
        return original(path, mode)

    monkeypatch.setattr(module.os, 'chmod', refused)
    with pytest.raises(PermissionError, match='private socket setup refused'):
        await runtime.serve()
    assert len(attempted) == 1 and runtime._lease == -1
    assert not runtime.location.socket.exists()
    assert not (runtime.location.directory / 'owner.json').exists()
    assert not [path for path in runtime.location.directory.iterdir() if path.name.startswith('.eyes-')]


@pytest.mark.parametrize('kind', ['file', 'unsafe-socket'])
async def test_runtime_unsafe_canonical_endpoint_still_fails_closed_without_replacement(tmp_path, kind):
    import socket

    from xdog.eyes.control import request
    from xdog.eyes.local_control import ControlError, runtime_location
    from xdog.eyes.runtime import ObserverRuntime

    runtime = ObserverRuntime(runtime_location(tmp_path))
    listener = None
    if kind == 'file':
        runtime.location.socket.write_text('preserve unsafe file')
    else:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(runtime.location.socket))
    runtime.location.socket.chmod(0o666)
    before = runtime.location.socket.lstat()
    try:
        with pytest.raises(ControlError, match='private owned Unix socket'):
            await runtime.serve()
        with pytest.raises(ControlError, match='private owned Unix socket'):
            await request(runtime.location, 'hello', {})
        after = runtime.location.socket.lstat()
        assert (after.st_dev, after.st_ino, after.st_mode) == (before.st_dev, before.st_ino, before.st_mode)
        assert runtime._lease == -1 and not (runtime.location.directory / 'owner.json').exists()
        assert not [path for path in runtime.location.directory.iterdir() if path.name.startswith('.eyes-')]
    finally:
        if listener is not None:
            listener.close()
        runtime.location.socket.unlink(missing_ok=True)
