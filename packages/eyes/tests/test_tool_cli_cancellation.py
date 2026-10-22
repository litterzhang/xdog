"""Finite CLI ownership, cancellation, and no private host daemon client."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

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


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
async def test_output_capacity_is_explicit_and_children_are_joined(monkeypatch, stream):
    import xdog.eyes.cli_process as module

    monkeypatch.setattr(module, '_MAX_OUTPUT', 1024)
    monkeypatch.setattr(module, 'command', lambda args: (sys.executable, '-c',
        f"import sys,time; sys.{stream}.write('x'*2048); sys.{stream}.flush(); time.sleep(60)"))
    runner = CliProcess()
    before = asyncio.all_tasks()
    with pytest.raises(ValueError, match='exceeds one MiB'):
        await asyncio.wait_for(runner.run(('list',)), 3)
    await runner.aclose()
    assert not (asyncio.all_tasks() - before)


async def test_tool_argv_and_nonzero_cli_output_are_preserved(monkeypatch, tmp_path):
    runner = CliProcess()
    binding = EyesCapability(runner)
    calls = []

    async def run(arguments, **kwargs):
        calls.append(arguments)
        return {'exit_code': 2, 'stdout': '', 'stderr': 'original CLI error\n'}

    monkeypatch.setattr(runner, 'run', run)
    result = json.loads(await binding.tools[0].execute('c', {
        'action': 'status', 'owner': '--not-an-option;$(shell)', 'observer_id': '--id'}))
    assert result == {'exit_code': 2, 'stdout': '', 'stderr': 'original CLI error\n'}
    assert calls == [('--owner=--not-an-option;$(shell)', 'status', '--', '--id')]
    await binding.tools[0].execute('c', {'action': 'create', 'owner': 'owner',
                                       'definition_path': str(tmp_path / 'saved.json')})
    assert calls[-1] == ('--owner=owner', 'start', str(tmp_path / 'saved.json'),
                         '--delivery', 'auto', '--no-wait')
    await binding.aclose()


@pytest.mark.parametrize('mode', ['event', 'task', 'close'])
async def test_cancel_after_durable_acceptance_does_not_retract_or_stop_daemon(tmp_path, monkeypatch, mode):
    import xdog.eyes.cli_process as module
    from xdog.eyes._daemon_control import DaemonControl

    from packages.eyes.tests.test_tool import raw_definition

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


async def test_provider_bootstrap_uses_only_public_cli_and_joins_cancelled_wait(monkeypatch, tmp_path):
    import xdog.eyes.cli_process as module

    ready = tmp_path / 'ready'
    args_seen = []

    def command(arguments):
        args_seen.append(arguments)
        return (sys.executable, '-c',
                'import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)', str(ready))

    monkeypatch.setattr(module, 'command', command)
    before = asyncio.all_tasks()
    opening = asyncio.create_task(provider.open(object()))
    async with asyncio.timeout(3):
        while not ready.exists():
            await asyncio.sleep(.01)
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(opening, 3)
    assert args_seen == [('daemon', 'start')]
    assert not (asyncio.all_tasks() - before)


def test_host_tool_and_provider_do_not_import_private_control_or_engine():
    import ast

    from xdog.eyes import capability, tool

    for module in (capability, tool):
        tree = ast.parse(Path(module.__file__).read_text())
        modules = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert 'xdog.eyes._daemon_control' not in modules
        assert not {'xdog.eyes.loader', 'xdog.eyes.daemon', 'xdog.eyes.runtime'} & set(modules)


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


@pytest.mark.parametrize('failure_point', ['_stop', '_finish'])
@pytest.mark.parametrize('mode', ['event', 'task', 'close'])
async def test_cleanup_failure_survives_simultaneous_cancellation(monkeypatch, tmp_path, mode, failure_point):
    import xdog.eyes.cli_process as module

    ready = tmp_path / 'ready'
    monkeypatch.setattr(module, 'command', lambda arguments: (sys.executable, '-c',
        'import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)', str(ready)))
    original = getattr(module, failure_point)

    async def failing(*args):
        await original(*args)  # real process and pipe owners are settled first
        raise OSError(f'injected real {failure_point} cleanup failure')

    monkeypatch.setattr(module, failure_point, failing)
    runner = CliProcess()
    cancel = asyncio.Event()
    before = asyncio.all_tasks()
    call = asyncio.create_task(runner.run(('list',), cancel=cancel))
    async with asyncio.timeout(3):
        while not ready.exists():
            await asyncio.sleep(.01)
    if mode == 'event':
        cancel.set()
    elif mode == 'task':
        call.cancel()
    else:
        with pytest.raises(OSError, match=f'{failure_point} cleanup failure'):
            await runner.aclose()
    # Never turn a real cleanup failure into just CancelledError, a receipt, or a
    # successful close. In the close case both waiters see the same owned failure.
    with pytest.raises(OSError, match=f'{failure_point} cleanup failure'):
        await asyncio.wait_for(call, 3)
    await runner.aclose()
    assert not runner._calls
    assert not (asyncio.all_tasks() - before)


@pytest.mark.parametrize('fail', [False, True])
async def test_repeated_aclose_cancellation_waits_owned_cleanup_and_reports_real_failure(monkeypatch, tmp_path, fail):
    import xdog.eyes.cli_process as module

    ready = tmp_path / 'ready'
    monkeypatch.setattr(module, 'command', lambda arguments: (sys.executable, '-c',
        'import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)', str(ready)))
    original = module._stop
    stopping = asyncio.Event()
    settle = asyncio.Event()

    async def slow_stop(process):
        await original(process)
        stopping.set()
        await settle.wait()
        if fail:
            raise OSError('owned stop failure after repeated close cancellation')

    monkeypatch.setattr(module, '_stop', slow_stop)
    runner = CliProcess()
    before = asyncio.all_tasks()
    call = asyncio.create_task(runner.run(('list',)))
    async with asyncio.timeout(3):
        while not ready.exists():
            await asyncio.sleep(.01)
    closing = asyncio.create_task(runner.aclose())
    await asyncio.wait_for(stopping.wait(), 3)
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()
    settle.set()
    if fail:
        with pytest.raises(OSError, match='owned stop failure'):
            await asyncio.wait_for(closing, 3)
        with pytest.raises(OSError, match='owned stop failure'):
            await asyncio.wait_for(call, 3)
    else:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, 3)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(call, 3)
    assert not runner._calls
    assert not (asyncio.all_tasks() - before)


async def test_multiple_cleanup_failures_are_all_reported_after_joining():
    from xdog.eyes.cli_process import _join

    async def fail(error):
        await asyncio.sleep(0)
        raise error

    tasks = (asyncio.create_task(fail(OSError('stop failure one'))),
             asyncio.create_task(fail(ValueError('pipe cleanup failure two'))))
    with pytest.raises(ExceptionGroup) as caught:
        await _join(tasks)
    assert [str(error) for error in caught.value.exceptions] == ['stop failure one', 'pipe cleanup failure two']
    assert all(task.done() for task in tasks)


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
async def test_infinite_output_flood_cannot_deadlock_cleanup_at_capacity(monkeypatch, stream):
    import xdog.eyes.cli_process as module

    monkeypatch.setattr(module, '_MAX_OUTPUT', 1024)
    monkeypatch.setattr(module, 'command', lambda arguments: (sys.executable, '-c',
        f"import os; data=b'x'*65536\nwhile True: os.write({1 if stream == 'stdout' else 2}, data)"))
    runner = CliProcess()
    before = asyncio.all_tasks()
    with pytest.raises(ValueError, match='exceeds one MiB'):
        await asyncio.wait_for(runner.run(('list',)), 3)
    await runner.aclose()
    assert not (asyncio.all_tasks() - before)
