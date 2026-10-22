"""Real subprocess-only consumers: scoped JSON commands, JSONL and detach."""
from __future__ import annotations

import asyncio
import json
import os
import select
import sys
from datetime import UTC, datetime

import pytest
from xdog.eyes.local_control import MAX_FRAME


@pytest.fixture
async def cli(tmp_path, isolated_daemon_directory, observer_data):
    from copy import deepcopy

    data = deepcopy(observer_data)
    data['sources'][0].update(root='.', include=['*.json'], poll_interval_ms=50)
    data['detectors'][0].update(debounce_ms=50, max_batch_delay_ms=100)
    data['handlers'] = [{'id': data['handlers'][0]['id'], 'type': 'events'}]
    base = [sys.executable, '-m', 'xdog.eyes', '--directory', str(isolated_daemon_directory),
            '--owner', 'goal:cli-only']
    children = []

    async def spawn(*args, stdout=asyncio.subprocess.PIPE):
        process = await asyncio.create_subprocess_exec(*base, *args, stdin=asyncio.subprocess.PIPE,
            stdout=stdout, stderr=asyncio.subprocess.PIPE, limit=MAX_FRAME, cwd=tmp_path)
        children.append(process)
        return process

    async def command(*args, stdin=None, expected=0):
        process = await spawn(*args)
        stdout, stderr = await asyncio.wait_for(process.communicate(stdin), 15)
        assert process.returncode == expected, (args, stdout, stderr)
        return stdout, stderr

    async def start(value=None, **kwargs):
        stdout, _ = await command('start', '-', '--delivery', 'events', '--key', 'tasks', '--no-wait',
                                  stdin=json.dumps(data if value is None else value).encode(), **kwargs)
        result = json.loads(stdout)
        await command('status', result['registration_id'], '--wait-ready', '--timeout', '5')
        return result

    try:
        yield command, spawn, start, data
    finally:
        # Closing consumers first prevents any implicit fixture ownership of sources.
        for process in children:
            if process.returncode is None:
                process.terminate()
        await asyncio.gather(*(process.wait() for process in children))
        # The autouse fixture owns only this test's isolated daemon shutdown.


async def test_stdin_registration_wait_ready_pause_and_explicit_ack_jsonl(cli, tmp_path):
    command, spawn, start, data = cli
    original = await start()
    duplicate = await start()
    assert duplicate['duplicate'] and duplicate['registration_id'] == original['registration_id']
    followed = await spawn('events', '--follow', '--limit', '1')
    followed.stdin.close()  # EOF on unused follow stdin is detach-neutral
    (tmp_path / 'task.json').write_text('{}')
    row = json.loads(await asyncio.wait_for(followed.stdout.readline(), 5))
    assert set(row) == {'sequence', 'key', 'registration_id', 'delivery'}
    assert row['key'] == 'tasks' and row['registration_id'] == original['registration_id']
    followed.terminate()
    await followed.wait()
    stdout, _ = await command('events', '--limit', '1')
    assert json.loads(stdout) == {'events': [row]}  # closing/terminating a consumer never ACKs
    stdout, _ = await command('ack', str(row['sequence']))
    assert json.loads(stdout)['acknowledged'] == row['sequence']
    assert json.loads((await command('events'))[0]) == {'events': []}
    assert json.loads((await command('events', '--after', '0'))[0]) == {'events': [row]}
    assert json.loads((await command('pause', 'tasks'))[0])['state'] == 'paused'
    assert json.loads((await command('start', '-', '--delivery', 'events', '--key', 'tasks', '--no-wait',
        stdin=json.dumps(data).encode()))[0])['state'] == 'paused'
    stdout, stderr = await command('status', 'tasks', '--wait-ready', '--timeout', '.1', expected=2)
    assert not stdout and b'not ready' in stderr
    assert json.loads((await command('status', 'tasks'))[0])['state'] == 'paused'
    await command('resume', 'tasks')
    await command('status', 'tasks', '--wait-ready', '--timeout', '5')


async def test_follow_reconnects_read_only_and_retains_original_owner_events(cli, tmp_path):
    command, spawn, start, _ = cli
    original = await start()
    followed = await spawn('events', '--follow', '--after', '0')
    followed.stdin.close()
    (tmp_path / 'first.json').write_text('1')
    first = json.loads(await asyncio.wait_for(followed.stdout.readline(), 5))
    await command('ack', str(first['sequence']))
    await command('daemon', 'stop')
    await asyncio.sleep(.3)
    assert followed.returncode is None
    status = json.loads((await command('daemon', 'status'))[0])
    assert status['state'] == 'not-running'  # follow MUST NOT start a replacement daemon
    (tmp_path / 'second.json').write_text('2')
    await command('daemon', 'start')
    second = json.loads(await asyncio.wait_for(followed.stdout.readline(), 8))
    assert second['registration_id'] == original['registration_id']
    assert second['sequence'] > first['sequence']
    assert (await command('events', '--after', str(first['sequence'])))[0].count(b'\n') == 1
    assert json.loads((await command('events', '--after', str(first['sequence'])))[0]) == {'events': [second]}
    followed.terminate()
    await followed.wait()


async def test_idle_follow_stdout_close_exits_without_changing_source_or_daemon(cli):
    command, spawn, start, _ = cli
    original = await start()
    read_fd, write_fd = os.pipe()
    try:
        followed = await spawn('events', '--follow', stdout=write_fd)
        os.close(write_fd)
        write_fd = -1
        os.close(read_fd)
        read_fd = -1
        assert await asyncio.wait_for(followed.wait(), 5) == 0
        assert json.loads((await command('status', 'tasks'))[0])['registration_id'] == original['registration_id']
        assert json.loads((await command('status', 'tasks'))[0])['state'] == 'watching'
        assert json.loads((await command('daemon', 'status'))[0])['state'] == 'running'
    finally:
        for fd in (read_fd, write_fd):
            if fd >= 0:
                os.close(fd)


async def test_full_output_pipe_detach_does_not_ack_partial_json_line(cli, tmp_path):
    command, spawn, start, data = cli
    # Publish all files atomically. Writing into an active watched directory can
    # create an early tiny batch under load, failing to exercise pipe backpressure.
    watched, staged = tmp_path / 'watched', tmp_path / 'staged'
    watched.mkdir()
    staged.mkdir()
    for index in range(160):
        (staged / (f'{index:03d}-' + 'x' * 180 + '.json')).write_text('{}')
    data['sources'][0]['root'] = str(watched)
    await start(data)
    read_fd, write_fd = os.pipe()
    try:
        followed = await spawn('events', '--follow', stdout=write_fd)
        os.close(write_fd)
        write_fd = -1
        os.replace(staged, watched)
        async with asyncio.timeout(5):
            while not select.select([read_fd], [], [], 0)[0]:
                await asyncio.sleep(.025)
        # Stop reading midway through a line larger than a pipe buffer. Neither
        # output backpressure nor EPIPE may become execution or ACK authority.
        os.close(read_fd)
        read_fd = -1
        assert await asyncio.wait_for(followed.wait(), 5) == 0
        stdout, _ = await command('events', '--limit', '1')
        row = json.loads(stdout)['events'][0]
        assert len(stdout) > 8192
        assert row['delivery']['events']
        assert (await command('events', '--limit', '1'))[0] == stdout
    finally:
        for fd in (read_fd, write_fd):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize('document', [b'', b'{} {}', b'{"version":2,"version":2}', b'{"x":NaN}',
                                      b' ' * MAX_FRAME],
                         ids=['empty', 'multiple', 'duplicate', 'nonfinite', 'oversized'])
async def test_bad_or_oversized_stdin_fails_before_daemon_start(cli, document):
    command, _, _, _ = cli
    stdout, stderr = await command('start', '-', '--delivery', 'events', '--no-wait',
                                   stdin=document, expected=2)
    assert not stdout and stderr and len(stderr) <= 2049
    assert json.loads((await command('daemon', 'status'))[0])['state'] == 'not-running'


async def test_source_checkpoint_json_recovers_accepted_past_deadline(cli, tmp_path):
    from xdog.eyes.loader import parse
    from xdog.eyes.sources.scheduler import SchedulerSource

    command, _, _, data = cli
    data['sources'] = [{'id': 'timer', 'type': 'scheduler', 'schedule': {
        'at': datetime.fromtimestamp(60., UTC).isoformat()}}]
    data['detectors'] = [{'id': 'due', 'type': 'scheduler.due', 'source': 'timer'}]
    data['routes'][0].update(detector='due', event_types=['scheduler.due'])
    model = parse(data, base_dir=tmp_path)
    source = SchedulerSource(model.sources[0], wall_now=0., monotonic_now=0.)
    checkpoint = source.export_checkpoint(wall_now=0., monotonic_now=0.)
    stdout, _ = await command('start', '-', '--delivery', 'events', '--no-wait',
        '--source-checkpoints-json', json.dumps({'timer': checkpoint}), stdin=json.dumps(data).encode())
    registration = json.loads(stdout)
    async with asyncio.timeout(5):
        while not (rows := json.loads((await command('events'))[0])['events']):
            await asyncio.sleep(.05)
    assert rows[0]['registration_id'] == registration['registration_id']
    assert rows[0]['delivery']['events'][0]['evidence'][0]['scheduled_at'] == 60.


async def test_wait_ready_without_daemon_times_out_read_only(cli):
    command, _, _, _ = cli
    stdout, stderr = await command('status', 'tasks', '--wait-ready', '--timeout', '.1', expected=2)
    assert not stdout and b'not ready within' in stderr
    assert json.loads((await command('daemon', 'status'))[0])['state'] == 'not-running'


def test_no_public_client_sdk_exports():
    from importlib.util import find_spec

    import xdog.eyes
    assert not hasattr(xdog.eyes, 'EyesClient') and not hasattr(xdog.eyes, 'Client')
    assert find_spec('xdog.eyes.client') is None


async def test_file_definition_roots_use_json_directory_not_cli_cwd(cli, tmp_path):
    command, _, _, data = cli
    definitions = tmp_path / 'definitions'
    definitions.mkdir()
    path = definitions / 'eyes.json'
    path.write_text(json.dumps(data))
    registration = json.loads((await command('start', str(path), '--delivery', 'events'))[0])
    (tmp_path / 'not-observed.json').write_text('{}')
    (definitions / 'observed.json').write_text('{}')
    async with asyncio.timeout(5):
        while not (events := json.loads((await command('events'))[0])['events']):
            await asyncio.sleep(.025)
    evidence = [item for row in events for event in row['delivery']['events'] for item in event['evidence']]
    assert {item['root'] for item in evidence} == {str(definitions)}
    assert {item['path'] for item in evidence} == {'observed.json'}
    assert 'workspace' not in registration
    await command('ack', str(events[-1]['sequence']))
