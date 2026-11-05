"""Finite CLI ownership, cancellation, and no private host daemon client."""
from __future__ import annotations

import asyncio
import json
import sys

import pytest
from xdog.eyes.capability import EyesCapability, provider
from xdog.eyes.cli_process import CliProcess, command


async def test_fixed_same_interpreter_argv_and_no_default_skill():
    assert command(('daemon', 'start')) == (sys.executable, '-m', 'xdog.eyes', 'daemon', 'start')
    assert provider.skill_names == ('eyes',)
    assert not getattr(provider, 'default_skills', ())


@pytest.mark.parametrize('mode', ['event', 'task', 'close'])
async def test_pending_call_cancellation_joins_spawn_pipes_and_event_watcher(monkeypatch, tmp_path, mode):
    import xdog.eyes.cli_process as module

    ready = tmp_path / 'ready'
    # No daemon or target. Exercise the actual subprocess lifecycle, not mocks of
    # communicate, including concurrent full stderr/stdout pipe readers.
    script = ("import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('ready'); "
              "sys.stdout.write('out'); sys.stdout.flush(); sys.stderr.write('err'); "
              "sys.stderr.flush(); time.sleep(60)")
    monkeypatch.setattr(module, 'command', lambda args: (sys.executable, '-c', script, str(ready)))
    before = asyncio.all_tasks()
    runner = CliProcess()
    binding = EyesCapability(runner)
    cancel = asyncio.Event()
    call = asyncio.create_task(binding.tools[0].execute('c', {'action': 'list', 'owner': 'literal;owner'}, cancel))
    async with asyncio.timeout(3):
        while not ready.exists():
            await asyncio.sleep(.01)
    if mode == 'event':
        cancel.set()
    elif mode == 'task':
        call.cancel()
        call.cancel()  # cleanup must settle even after repeated host cancellation
    else:
        await binding.aclose()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(call, 3)
    await binding.aclose()
    await asyncio.sleep(0)
    assert not runner._calls
    assert not (asyncio.all_tasks() - before)


@pytest.mark.parametrize('mode', ['event', 'task', 'close'])
async def test_cancel_after_durable_acceptance_does_not_retract_or_stop_daemon(tmp_path, monkeypatch, mode):
    import xdog.eyes.cli_process as module
    from xdog.eyes._daemon_control import DaemonControl

    from packages.eyes.tests.tool_helpers import raw_definition

    binding = await provider.open(object())
    path = tmp_path / 'eyes.json'
    path.write_text(json.dumps(raw_definition(tmp_path)))
    control = DaemonControl(owner='accepted')
    generation = (await control.start_daemon())
    assert generation['state'] == 'running'
    original_generation = control.location.generation
    received = asyncio.Event()
    original_read = module._read

    async def hold(reader):
        result = await original_read(reader)
        # Hold the receipt in transit, after CLI received the durable commit.
        if b'registration_id' in result:
            received.set()
            await asyncio.Event().wait()
        return result

    monkeypatch.setattr(module, '_read', hold)
    cancel = asyncio.Event()
    call = asyncio.create_task(binding.tools[0].execute('create', {
        'action': 'create', 'owner': 'accepted', 'definition_path': str(path)}, cancel))
    try:
        await asyncio.wait_for(received.wait(), 5)
        identifier = (await control.status())['registrations'][0]['registration_id']
        if mode == 'event':
            cancel.set()
        elif mode == 'task':
            call.cancel()
        else:
            await binding.aclose()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(call, 3)
        await binding.aclose()
        assert (await control.daemon_status())['state'] == 'running'
        assert control.location.generation == original_generation
        await control.wait_ready(identifier, timeout=3)
        rows = (await control.status())['registrations']
        assert len(rows) == 1 and rows[0]['registration_id'] == identifier
        assert rows[0]['state'] == 'watching'
    finally:
        call.cancel()
        await asyncio.gather(call, return_exceptions=True)
        await binding.aclose()
        await control.aclose()


async def test_cancellation_while_spawn_handle_is_unpublished_settles_exact_child(monkeypatch, tmp_path):
    import xdog.eyes.cli_process as module

    spawning = asyncio.Event()
    publish = asyncio.Event()
    real_spawn = asyncio.create_subprocess_exec
    children = []

    async def delayed_spawn(*args, **kwargs):
        process = await real_spawn(sys.executable, '-c', 'import time; time.sleep(60)', **kwargs)
        children.append(process)
        spawning.set()
        await publish.wait()
        return process

    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', delayed_spawn)
    runner = CliProcess()
    before = asyncio.all_tasks()
    call = asyncio.create_task(runner.run(('list',)))
    await asyncio.wait_for(spawning.wait(), 3)
    call.cancel()
    await asyncio.sleep(0)
    call.cancel()
    closing = asyncio.create_task(runner.aclose())
    await asyncio.sleep(0)
    assert not closing.done()  # actual OS spawn must settle before close returns
    publish.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(call, 3)
    await asyncio.wait_for(closing, 3)
    assert len(children) == 1 and children[0].returncode is not None
    assert not (asyncio.all_tasks() - before)


async def test_pre_cancelled_or_closed_binding_never_spawns(monkeypatch):
    import xdog.eyes.cli_process as module

    async def forbidden(*args, **kwargs):
        raise AssertionError('No subprocess may be created')

    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', forbidden)
    runner = CliProcess()
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await runner.run(('list',), cancel=cancel)
    await runner.aclose()
    with pytest.raises(RuntimeError, match='closed'):
        await runner.run(('list',))


async def test_real_nonzero_cli_exit_is_failure_not_a_successful_registration_receipt():
    binding = await provider.open(object())
    try:
        result = json.loads(await binding.tools[0].execute('bad-id', {
            'action': 'cancel', 'owner': 'explicit-owner', 'observer_id': 'missing'}))
        assert result['exit_code'] == 2
        assert result['stdout'] == ''
        assert 'Unknown Eyes registration' in result['stderr']
        assert 'registration_id' not in result
        listed = json.loads(await binding.tools[0].execute('list', {'action': 'list', 'owner': 'explicit-owner'}))
        assert listed['exit_code'] == 0
        assert json.loads(listed['stdout']) == {'registrations': []}
        assert 'exit_code != 0' in binding.tools[0].description
        assert 'exit_code != 0' in binding.tools[0].parameters['properties']['action']['description']
    finally:
        await binding.aclose()
