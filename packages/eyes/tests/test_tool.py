"""Thin tool and CLI share a real daemon; JSON alone selects destinations."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from xdog.eyes._cli_io import _read_definition
from xdog.eyes._daemon_control import DaemonControl
from xdog.eyes.capability import provider


async def invoke(binding, action, *, owner='caller-selected', **fields):
    result = json.loads(await binding.tools[0].execute('call',
        {'action': action, 'owner': owner, **fields}))
    assert result['exit_code'] == 0, result['stderr']
    return json.loads(result['stdout'])


def raw_definition(root):
    return {'version': 2, 'name': 'raw-events',
            'sources': [{'id': 'files', 'type': 'file', 'root': str(root), 'include': ['*.txt'],
                         'poll_interval_ms': 50}],
            'detectors': [{'id': 'change', 'type': 'file.change', 'source': 'files',
                           'debounce_ms': 50, 'max_batch_delay_ms': 100}],
            'handlers': [{'id': 'sink', 'type': 'events'}],
            'routes': [{'id': 'route', 'detector': 'change', 'handler': 'sink', 'event_types': ['file.created']}]}


async def test_register_only_starts_daemon_and_close_leaves_it_running(tmp_path, monkeypatch):
    import xdog.eyes.capability as module

    assert provider.requirements == ()  # No dependency on a host ACP receiver.
    binding = await provider.open(object())
    check = DaemonControl(owner='check')
    assert (await check.daemon_status())['registrations'] == 0
    assert {item.name for item in binding.tools} == {'eyes'}
    assert not (tmp_path / 'observers').exists()
    assert not list(tmp_path.glob('*.sqlite3'))
    await binding.aclose()
    assert binding.control.closed
    check = DaemonControl(owner='check')
    try:
        assert (await check.daemon_status())['state'] == 'running'
    finally:
        await check.aclose()
    with pytest.raises(RuntimeError, match='closed'):
        await invoke(binding, 'list')
    # No host-specific IO or import can be hidden behind generic callback names.
    source = Path(module.__file__).read_text()
    for name in ('ManagedObservers', 'ObserverJournal', 'register_output_source', 'capture_reply', 'input_get'):
        assert name not in source


async def test_tool_namespace_matches_real_cli_and_survives_daemon_restart(tmp_path):
    binding = await provider.open(object())
    path = tmp_path / 'eyes.json'
    path.write_text(json.dumps(raw_definition(tmp_path)))
    owner = 'caller-selected'
    result = await invoke(binding, 'create', definition_path=str(path))
    identifier = result['registration_id']
    control = DaemonControl(owner=owner)
    try:
        await control.wait_ready(identifier, timeout=5)
        assert (await invoke(binding, 'create', definition_path=str(path)))['registration_id'] == identifier
        assert (await invoke(binding, 'pause', observer_id=identifier))['state'] == 'paused'
        assert (await invoke(binding, 'create', definition_path=str(path)))['state'] == 'paused'
        await invoke(binding, 'resume', observer_id=identifier)
        await control.wait_ready(identifier, timeout=5)
        # Same real CLI protocol and inventory, not a second host journal.
        proc = await asyncio.create_subprocess_exec(sys.executable, '-m', 'xdog.eyes', '--owner',
            owner, 'status', identifier,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), 10)
        assert proc.returncode == 0, err
        assert json.loads(out)['registration_id'] == identifier
        await binding.aclose()
        await control.stop_daemon()
        replacement = await provider.open(object())
        try:
            status = await invoke(replacement, 'status', observer_id=identifier)
            assert status['registration_id'] == identifier
            assert (await invoke(replacement, 'cancel', observer_id=identifier))['state'] == 'cancelled'
        finally:
            await replacement.aclose()
    finally:
        await binding.aclose()
        await control.aclose()


async def test_json_multi_source_external_roots_and_third_party_destination_work_without_host(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    record = outside / 'peer.jsonl'
    data = raw_definition(outside)
    data['handlers'] = [{'id': 'sink', 'type': 'acp', 'session': 'target', 'delivery': 'enqueue',
        'cwd': str(outside), 'instruction': 'Record incoming evidence only', 'connection': {
            'transport': 'stdio', 'command': [sys.executable,
                str(Path(__file__).with_name('fixtures') / 'acp_agent.py'), '--record', str(record),
                '--mode', 'inbox-success']}}]
    data['sources'].append({**data['sources'][0], 'id': 'second', 'include': ['*.data']})
    data['detectors'].append({**data['detectors'][0], 'id': 'second-change', 'source': 'second'})
    data['routes'].append({**data['routes'][0], 'id': 'second-route', 'detector': 'second-change'})
    path = outside / 'eyes.json'  # No management workspace is needed.
    path.write_text(json.dumps(data))
    binding = await provider.open(object())
    owner = 'caller-selected'
    control = DaemonControl(owner=owner)
    try:
        created = await invoke(binding, 'create', definition_path=str(path))
        identifier = created['registration_id']
        await control.wait_ready(identifier, timeout=5)
        await binding.aclose()  # There is no host event consumer to keep alive.
        (outside / 'first.txt').write_text('one')
        (outside / 'second.data').write_text('two')
        async with asyncio.timeout(8):
            while True:
                rows = [json.loads(line) for line in record.read_text().splitlines()]
                if len([row for row in rows if row['kind'] == 'stored']) == 2:
                    break
                await asyncio.sleep(.025)
        enqueued = [row['params'] for row in rows
                    if row['kind'] == 'extension' and row['method'] == 'xdog/enqueue']
        assert len(enqueued) == 2
        assert all(row['session_id'] == 'target' and row['cwd'] == str(outside) for row in enqueued)
        assert not any(row['kind'] in {'prompt', 'received', 'load'} for row in rows)
        assert not any(row['kind'] == 'extension' and row['params'].get('message_id')
                       and row['method'] != 'xdog/enqueue' for row in rows)
        await control.cancel(identifier)
    finally:
        await binding.aclose()
        await control.aclose()


@pytest.mark.parametrize('kind', ['symlink', 'directory', 'fifo', 'oversized', 'duplicates'])
async def test_definition_file_reader_is_bounded_and_never_blocks_on_special_files(tmp_path, kind):
    path = tmp_path / 'definition.json'
    if kind == 'symlink':
        (tmp_path / 'real.json').write_text('{}')
        path.symlink_to(tmp_path / 'real.json')
    elif kind == 'directory':
        path.mkdir()
    elif kind == 'fifo':
        import os
        os.mkfifo(path)
    elif kind == 'oversized':
        path.write_bytes(b' ' * (1024 * 1024 + 1))
    else:
        path.write_text('{"name":"one","name":"two"}')
    with pytest.raises((ValueError, OSError)):
        await asyncio.wait_for(asyncio.to_thread(_read_definition, str(path)), 2)


async def test_tool_requires_absolute_definition_path_and_owner_only_management(tmp_path, monkeypatch):
    binding = await provider.open(object())
    path = tmp_path / 'eyes.json'
    data = raw_definition('.')
    path.write_text(json.dumps(data))
    control = DaemonControl(owner='caller-selected')
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    try:
        assert binding.tools[0].parameters['required'] == ['action', 'owner']
        assert 'workspace' not in binding.tools[0].parameters['properties']
        with pytest.raises(ValueError, match='absolute'):
            await invoke(binding, 'create', definition_path='eyes.json')
        assert (await invoke(binding, 'list'))['registrations'] == []
        with pytest.raises(ValueError, match='unexpected'):
            await invoke(binding, 'list', workspace=str(tmp_path))
        created = await invoke(binding, 'create', definition_path=str(path))
        assert 'workspace' not in created
        await control.wait_ready(created['registration_id'], timeout=5)
        # The JSON parent, not the tool/daemon cwd, owns relative source roots.
        (tmp_path / 'observed.txt').write_text('one')
        rows = await control.events(wait=3)
        assert rows[0]['delivery']['events'][0]['evidence'][0]['root'] == str(tmp_path)
        path.write_text('{}')  # Accepted snapshots do not reread definitions.
        await control.ack(rows[0]['sequence'])
        assert (await invoke(binding, 'status', observer_id=created['registration_id']))['state'] == 'watching'
        await invoke(binding, 'cancel', observer_id=created['registration_id'])
    finally:
        await control.aclose()
        await binding.aclose()
