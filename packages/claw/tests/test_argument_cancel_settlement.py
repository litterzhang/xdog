"""Real-gateway cancellation receipts settle only their original reservation."""
import asyncio
import json
import shutil
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer


@pytest.mark.parametrize('ending', ['cancel', 'duplicate-cancel', 'withdraw'])
async def test_unstarted_cancel_receipt_precedes_successor_without_borrowed_reservation(tmp_path, monkeypatch, ending):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/argcomplete',
                    root / 'argcomplete')
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'batch.sock'), pid_file=str(tmp_path / 'batch.pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),),
        extensions=('argcomplete',), extension_dir=str(root))
    server = GatewayServer(config)
    def no_model(*args):
        pytest.fail('Argument inspection must never run a model')
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, no_model)):
        await server.start()
    orchestrator = server._orchestrator
    owner = orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
    await owner.initialize_extensions()
    identity = owner.extensions.command_completions()['ext-argcomplete-deploy']
    entered, trace = [], []
    def record(event, **value):
        trace.append({'t': time.monotonic(), 'event': event, **value})
    complete = orchestrator.complete_extension_argument
    async def observed(*args, **kwargs):
        entered.append(args)
        return await complete(*args, **kwargs)
    monkeypatch.setattr(orchestrator, 'complete_extension_argument', observed)
    for name in ('try_reserve_group', 'release_group_reservation'):
        original = getattr(orchestrator, name)
        def wrap(*args, _name=name, _original=original, **kwargs):
            result = _original(*args, **kwargs)
            record(_name, result=bool(result) if _name == 'try_reserve_group' else result)
            return result
        monkeypatch.setattr(orchestrator, name, wrap)
    write = asyncio.StreamWriter.write
    def wire(writer, data):
        for line in data.splitlines():
            value = json.loads(line)
            if value.get('type') in {'argument_completion_result', 'extension_result'}:
                record('receipt-published', value=value, busy=orchestrator.is_group_running('main'))
        return write(writer, data)
    monkeypatch.setattr(asyncio.StreamWriter, 'write', wire)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def read(kind):
        async with asyncio.timeout(5):
            while True:
                value = json.loads(await reader.readline())
                if value['type'] == kind:
                    return value
    try:
        writer.write((json.dumps({'type': 'ping', 'group_id': 'main', 'extension_argument_completion': 1,
            'extension_argument_cancel': 1, 'extension_controls': 1}) + '\n').encode())
        await writer.drain()
        await read('pong')
        original = {'type': 'argument_completion', 'request_id': 'never-started', 'group_id': 'main',
            'session_id': owner.meta.session_id, 'command': 'ext-argcomplete-deploy',
            'identity': identity, 'prefix': 'a'}
        cancel = ({'type': 'argument_completion_cancel', 'request_id': 'never-started', 'group_id': 'main',
            'session_id': owner.meta.session_id} if ending != 'withdraw' else
            {'type': 'ping', 'group_id': 'main', 'extension_argument_completion': 0})
        successor = {'type': 'extension_control', 'request_id': 'counts', 'group_id': 'main',
            'session_id': owner.meta.session_id, 'action': 'command', 'name': 'ext-argcomplete-counts', 'args': ''}
        frames = [original, cancel, *([cancel] if ending == 'duplicate-cancel' else []), successor]
        writer.write(''.join(json.dumps(frame) + '\n' for frame in frames).encode())
        await writer.drain()
        canceled = await read('argument_completion_result')
        result = await read('extension_result')
        assert canceled['status'] == 'canceled'
        assert not entered, 'Regression requires the original worker never entered'
        canceled_receipts = [event for event in trace if event['event'] == 'receipt-published'
                             and event['value']['type'] == 'argument_completion_result']
        assert len(canceled_receipts) == 1 and not canceled_receipts[0]['busy']
        assert result['success'], result
        assert json.loads(result['output']) == {'executed': 0, 'completed': 0}
        # No early release plus later done-callback release may steal the successor.
        reservations = [event['result'] for event in trace if event['event'] == 'try_reserve_group']
        assert reservations == [True, True]
        assert not orchestrator.is_group_running('main')
    finally:
        (tmp_path / ('batch-' + ending + '.json')).write_text(json.dumps(trace, indent=2))
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_started_cancel_reader_stays_responsive_until_original_cleanup(tmp_path, monkeypatch):
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'started.sock'), pid_file=str(tmp_path / 'started.pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, None)):
        await server.start()
    owner = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
    entered, canceling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def slow_cleanup(group, *args, **kwargs):
        async with server._orchestrator.group_controller(group).acquire(is_user=True):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                canceling.set()
                await release.wait()
    monkeypatch.setattr(server._orchestrator, 'complete_extension_argument', slow_cleanup)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(*packets):
        writer.write(''.join(json.dumps(packet) + '\n' for packet in packets).encode())
        await writer.drain()
    async def read(kind):
        async with asyncio.timeout(5):
            while True:
                value = json.loads(await reader.readline())
                if value['type'] == kind:
                    return value
    try:
        await send({'type': 'ping', 'group_id': 'main', 'extension_argument_completion': 1,
                    'extension_argument_cancel': 1})
        await read('pong')
        scope = {'group_id': 'main', 'session_id': owner.meta.session_id}
        await send({'type': 'argument_completion', 'request_id': 'started', **scope,
                    'command': 'ext-test-run', 'identity': 'a' * 32, 'prefix': 'a'})
        await asyncio.wait_for(entered.wait(), 5)
        await send({'type': 'argument_completion_cancel', 'request_id': 'started', **scope}, {'type': 'ping'})
        await asyncio.wait_for(canceling.wait(), 5)
        await read('pong')  # Original cleanup is deliberately held; cancellation must not block the reader.
        assert not release.is_set() and server._orchestrator.is_group_running('main')
        await send({'type': 'extension_control', 'request_id': 'too-early', **scope, 'action': 'list'})
        assert not (await read('extension_result'))['success']
        release.set()
        assert (await read('argument_completion_result'))['status'] == 'canceled'
        assert not server._orchestrator.is_group_running('main')
        await send({'type': 'extension_control', 'request_id': 'settled', **scope, 'action': 'list'})
        assert (await read('extension_result'))['success']
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()
        await server.stop()
