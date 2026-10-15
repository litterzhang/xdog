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
    from xdog.eyes.local_control import runtime_location

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
        child = process._children[str(runtime_location(tmp_path).directory)]
        assert await asyncio.to_thread(main,['start',str(definition)])==0
        assert json.loads(capsys.readouterr().out)['registration_id']==identifier
        async with asyncio.timeout(5):
            while True:
                assert await asyncio.to_thread(main,['status',identifier])==0
                row = json.loads(capsys.readouterr().out)['registrations'][0]
                if row['sources']['files']=='watching':
                    break
                await asyncio.sleep(0.02)
        (tmp_path/'src'/'a.py').write_text('changed')
        await wait_for(lambda:any(kind=='prompt' for kind,value in events))
        assert await asyncio.to_thread(main,['stop',identifier])==0
        assert json.loads(capsys.readouterr().out)['stopped'] is True
        assert await asyncio.to_thread(child.wait,timeout=5)==0
        assert not runtime_location(tmp_path).socket.exists()
    finally:
        if identifier is not None and child is not None and child.poll() is None:
            await asyncio.to_thread(main,['stop',identifier])
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


async def test_crashed_runtime_is_not_restarted_by_status_and_explicit_start_rebaselines(
    tmp_path,observer_data,unix_peer,monkeypatch,capsys
):
    import json

    from xdog.eyes import process
    from xdog.eyes.cli import main
    from xdog.eyes.local_control import runtime_location

    path,events,gate,writers = unix_peer
    (tmp_path/'src').mkdir()
    changed = tmp_path/'src'/'a'
    changed.write_text('baseline')
    document = tmp_path/'eyes.json'
    document.write_text(json.dumps(unix_definition(observer_data,path,tmp_path)))
    monkeypatch.chdir(tmp_path)
    assert await asyncio.to_thread(main,['start',str(document)])==0
    first = json.loads(capsys.readouterr().out)['registration_id']
    location = runtime_location(tmp_path)
    child = process._children[str(location.directory)]
    child.kill()
    await asyncio.to_thread(child.wait,timeout=5)
    changed.write_text('changed while no observer exists')
    assert await asyncio.to_thread(main,['status'])==2
    capsys.readouterr()
    assert len([e for e in events if e[0]=='initialize'])==1
    assert await asyncio.to_thread(main,['start',str(document)])==0
    second = json.loads(capsys.readouterr().out)['registration_id']
    assert first!=second
    new_child = process._children[str(location.directory)]
    try:
        async with asyncio.timeout(5):
            while True:
                await asyncio.to_thread(main,['status',second])
                row = json.loads(capsys.readouterr().out)['registrations'][0]
                if row['sources']['files']=='watching':
                    break
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)
        assert not [e for e in events if e[0]=='prompt']
    finally:
        await asyncio.to_thread(main,['stop',second])
        await asyncio.to_thread(new_child.wait,timeout=5)
