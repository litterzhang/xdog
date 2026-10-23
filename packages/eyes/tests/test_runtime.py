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


async def test_concurrent_starters_share_daemon_generation_and_registration(tmp_path, observer_data, unix_peer):
    from xdog.eyes._daemon_control import DaemonControl
    from xdog.eyes.loader import parse

    path, events, gate, writers = unix_peer
    (tmp_path / 'src').mkdir()
    data = parse(unix_definition(observer_data, path, tmp_path), base_dir=tmp_path)

    async def start_one():
        control = await DaemonControl.open(owner='concurrent')
        try:
            result = await control.start(data)
            return control.location, result
        finally:
            await control.aclose()

    results = await asyncio.gather(*(start_one() for _ in range(4)))
    assert len({item[0].generation for item in results}) == 1
    identifiers = {item[1]['registration_id'] for item in results}
    assert len(identifiers) == 1
    assert len([event for event in events if event[0] == 'initialize']) == 1
    async with DaemonControl(owner='concurrent') as control:
        await control.cancel(identifiers.pop())
        # Last registration cancellation never stops the shared daemon.
        assert (await control.daemon_status())['state'] == 'running'
        await control.stop_daemon()


async def test_daemon_shutdown_settles_pending_handler_activation(tmp_path, observer_data, unix_peer):
    from xdog.eyes._daemon_control import DaemonControl
    from xdog.eyes.control import request
    from xdog.eyes.loader import parse
    from xdog.eyes.local_control import daemon_location
    from xdog.eyes.runtime import DaemonRuntime

    path, events, gate, writers = unix_peer
    runtime = DaemonRuntime(daemon_location())
    serving = asyncio.create_task(runtime.serve())
    control = DaemonControl(owner='pending-start')
    try:
        await wait_for(runtime.location.socket.exists)
        await request(runtime.location, 'hello', {})
        gate.clear()
        data = parse(unix_definition(observer_data, path, tmp_path), base_dir=tmp_path)
        result = await control.start(data, wait_ready=False)
        identifier = result['registration_id']
        await wait_for(lambda: any(event[0] == 'initialize' for event in events))
        active = runtime.daemon.active[identifier]
        assert active.task is not None and not active.task.done()
        runtime.stop.set()
        await asyncio.wait_for(serving, 3)
        assert active.task.done() and not runtime.daemon.active
        await wait_for(lambda: not writers)
    finally:
        gate.set()
        runtime.stop.set()
        await asyncio.gather(serving, return_exceptions=True)
        await control.aclose()


async def test_crashed_daemon_status_is_readonly_and_explicit_start_restores_original_cursor(
    tmp_path,observer_data,unix_peer,monkeypatch,capsys
):
    import hashlib
    import json

    from xdog.eyes import process
    from xdog.eyes.cli import main
    from xdog.eyes.daemon_store import DaemonStore
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
    # Inspect only after SIGKILL: reading before it could let the initial cursor
    # commit catch up and hide start acknowledging an in-memory-only baseline.
    saved, = await DaemonStore(location.directory).rows()
    assert saved['checkpoint'] is not None, 'start reported ready before persisting its file baseline'
    baseline = json.loads(saved['checkpoint'])['detectors']['changes']['baseline']
    assert dict(baseline)['src/a']['digest'] == hashlib.sha256(b'baseline').hexdigest()
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
    from xdog.eyes.local_control import daemon_location, read_location, validate_socket

    runtime = module.DaemonRuntime(daemon_location())
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
    from xdog.eyes.local_control import ControlError, daemon_location, validate_socket

    runtime = module.DaemonRuntime(daemon_location())
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
    from xdog.eyes.local_control import daemon_location

    runtime = module.DaemonRuntime(daemon_location())
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
    from xdog.eyes.local_control import ControlError, daemon_location
    from xdog.eyes.runtime import DaemonRuntime

    runtime = DaemonRuntime(daemon_location())
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
