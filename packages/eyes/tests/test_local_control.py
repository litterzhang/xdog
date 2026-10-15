"""Eyes controls only its own runtime; no harness capabilities or agent delivery."""

import asyncio
import fcntl
import os
import subprocess
from dataclasses import replace

import pytest


@pytest.mark.parametrize('value', [b'{"v":1,"v":2}\n', b'{"x":NaN}\n', b'{"x":Infinity}\n', b'[]\n', b'{}'])
def test_control_frames_reject_ambiguous_or_unframed_json(value):
    from xdog.eyes.local_control import ControlError, decode_frame

    with pytest.raises(ControlError):
        decode_frame(value)


def test_location_is_owned_by_cwd_not_definition_name_and_has_no_harness_token(tmp_path):
    from xdog.eyes.local_control import runtime_location

    first = runtime_location(tmp_path)
    assert first==runtime_location(tmp_path)
    assert first.cwd==tmp_path.resolve() and first.generation==''
    assert first.directory.stat().st_mode & 0o777==0o700
    assert first!=runtime_location(tmp_path.parent)
    assert not hasattr(first,'token') and not hasattr(first,'descriptor')


def test_runtime_file_validation_precedes_truncation(tmp_path):
    from xdog.eyes.local_control import ControlError, private_open

    path = tmp_path/'existing'
    path.write_text('preserve me')
    path.chmod(0o644)
    with pytest.raises(ControlError):
        private_open(path,os.O_WRONLY|os.O_TRUNC)
    assert path.read_text()=='preserve me'


def test_live_owner_lease_without_record_never_starts_another_process(tmp_path,monkeypatch):
    from xdog.eyes.local_control import private_open, runtime_location
    from xdog.eyes.process import ensure_runtime

    location = runtime_location(tmp_path)
    lock = private_open(location.directory/'owner.lock',os.O_CREAT|os.O_RDWR)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def forbidden(*args,**kwargs):
        raise AssertionError('a live owner lease is not a dead process')

    monkeypatch.setattr(subprocess,'Popen',forbidden)
    try:
        with pytest.raises(TimeoutError):
            ensure_runtime(location,timeout=0.1)
    finally:
        os.close(lock)


async def test_live_unresponsive_socket_is_not_replaced_or_restarted(tmp_path,monkeypatch):
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.process import ensure_runtime

    location = runtime_location(tmp_path)
    writers = []

    def accept(reader,writer):
        writers.append(writer)

    server = await asyncio.start_unix_server(accept,path=str(location.socket))
    location.socket.chmod(0o600)
    inode = location.socket.stat().st_ino

    def forbidden(*args,**kwargs):
        raise AssertionError('handshake timeout cannot prove death')

    monkeypatch.setattr(subprocess,'Popen',forbidden)
    try:
        with pytest.raises(TimeoutError):
            await asyncio.to_thread(ensure_runtime,location,timeout=0.15)
        assert location.socket.stat().st_ino==inode
    finally:
        for writer in writers:
            writer.close()
        server.close()
        await server.wait_closed()
        location.socket.unlink(missing_ok=True)


async def test_old_generation_cannot_stop_another_runtime(tmp_path):
    from xdog.eyes.control import request
    from xdog.eyes.local_control import runtime_location
    from xdog.eyes.runtime import ObserverRuntime

    runtime = ObserverRuntime(runtime_location(tmp_path))
    task = asyncio.create_task(runtime.serve())
    try:
        async with asyncio.timeout(3):
            while True:
                try:
                    await request(runtime.location,'hello',{})
                    break
                except (OSError,ValueError):
                    await asyncio.sleep(0.01)
        stale = replace(runtime.location,generation='0'*32)
        response = await request(stale,'stop',{'registration_id':'missing'})
        assert response['status']=='stale-generation' and not task.done()
        assert (await request(runtime.location,'hello',{}))['status']=='ok'
        invalid = await request(runtime.location,'deliver',{'payload':'not an agent host'})
        assert invalid['status']=='rejected'
    finally:
        runtime.stop.set()
        await asyncio.wait_for(task,3)


async def test_cancelled_publication_settles_before_releasing_control_lease(tmp_path,monkeypatch):
    import threading

    from xdog.eyes import runtime as module
    from xdog.eyes.local_control import runtime_location

    entered,release = threading.Event(),threading.Event()
    original = module.publish_location

    def delayed(location):
        entered.set()
        release.wait(4)
        return original(location)

    monkeypatch.setattr(module,'publish_location',delayed)
    runtime = module.ObserverRuntime(runtime_location(tmp_path))
    task = asyncio.create_task(runtime.serve())
    try:
        assert await asyncio.to_thread(entered.wait,3)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done(), 'an owner record publisher must not outlive its lease'
    finally:
        release.set()
        await asyncio.gather(task,return_exceptions=True)
    assert not runtime.location.socket.exists()
    assert not (runtime.location.directory/'owner.json').exists()


async def test_control_response_schema_errors_are_reported_as_control_errors(tmp_path):
    from xdog.eyes.control import request
    from xdog.eyes.local_control import ControlError, encode_frame, runtime_location

    location = runtime_location(tmp_path)
    tasks = set()

    async def answer(reader,writer):
        try:
            await reader.readline()
            writer.write(encode_frame({'version':1,'generation':'1'*32,'cwd':str(tmp_path),
                                       'status':[],'body':{}}))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def accepted(reader,writer):
        task = asyncio.create_task(answer(reader,writer))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    server = await asyncio.start_unix_server(accepted,path=str(location.socket))
    location.socket.chmod(0o600)
    try:
        with pytest.raises(ControlError):
            await request(location,'hello',{})
    finally:
        server.close()
        await server.wait_closed()
        if tasks:
            await asyncio.gather(*tasks)
        location.socket.unlink(missing_ok=True)
