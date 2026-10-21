"""Real SDK/Unix transport tests; server stores inputs, never constructs an Agent."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from xdog.agent.acp.client import enqueue, inbox_status
from xdog.agent.session_input import SessionInput


class Receiver:
    def __init__(self, root):
        self.target = {'endpoint': str(root / 'acp.sock'), 'cwd': str(root), 'session_id': 'session',
                       'inbox_id': 'namespace', 'branch_id': 'branch'}
        self.rows = {}
        self.calls = []
        self.tasks = set()
        self.drop_ack = False
        self.drop_before_commit = False
        self.extensions = {'enqueue': 1, 'inbox_status': 1}
        self.hold_enqueue = None
        self.rotate_after_commit = False
        self.entered = asyncio.Event()

    async def peer(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            while line := await reader.readline():
                request = json.loads(line)
                method, params = request['method'], request.get('params', {})
                self.calls.append((method, params))
                error = None
                if method == 'initialize':
                    result = {'protocolVersion': 1, 'agentCapabilities': {},
                              '_meta': {'xdog': {'extensions': self.extensions}}}
                elif method == '_xdog/inbox_status':
                    rows = [{'message_id': key, 'branch_id': self.target['branch_id'], 'status': 'pending'}
                            for key in self.rows if params.get('message_id', key) == key]
                    result = {**{k: v for k, v in self.target.items() if k != 'endpoint'}, 'messages': rows}
                elif method == '_xdog/enqueue':
                    self.entered.set()
                    if self.hold_enqueue is not None:
                        await self.hold_enqueue.wait()
                    identifier = params['message_id']
                    existing = self.rows.get(identifier)
                    if existing is not None and existing != params:
                        error = {'code': -32602, 'message': 'Original ID content differs'}
                    elif self.drop_before_commit:
                        self.drop_before_commit = False
                        break
                    else:
                        self.rows[identifier] = params
                        if self.rotate_after_commit:
                            self.target['inbox_id'] = 'replacement-inbox'
                    result = {'session_id': self.target['session_id'], 'branch_id': self.target['branch_id'],
                              'message_id': identifier, 'status': 'duplicate' if existing else 'queued',
                              'message_state': 'pending'}
                    if self.drop_ack:
                        self.drop_ack = False
                        break
                else:
                    raise AssertionError('Unexpected method: ' + method)
                response = {'jsonrpc': '2.0', 'id': request['id']}
                response['error' if error else 'result'] = error or result
                writer.write(json.dumps(response).encode() + b'\n')
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            self.tasks.discard(task)


@pytest.fixture
async def receiver():
    pytest.importorskip('acp')
    with tempfile.TemporaryDirectory(prefix='xd-acp-') as name:
        root = Path(name)
        os.chmod(root, 0o700)
        receiver = Receiver(root)
        server = await asyncio.start_unix_server(receiver.peer, receiver.target['endpoint'])
        os.chmod(receiver.target['endpoint'], 0o600)
        try:
            yield receiver
        finally:
            server.close()
            tasks = tuple(receiver.tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await server.wait_closed()


def message(instruction='Review authoritative tasks'):
    return SessionInput('original-id', 'automatic', 'Goal', instruction, (), {'goal_id': 'g'})


async def test_real_transport_only_negotiated_durable_intake(receiver):
    assert (await inbox_status(receiver.target))['inbox_id'] == 'namespace'
    result = await enqueue(receiver.target, message())
    assert result['status'] == 'queued'
    assert (await enqueue(receiver.target, message()))['status'] == 'duplicate'
    assert set(receiver.rows) == {'original-id'}
    assert {method for method, _ in receiver.calls} == {'initialize', '_xdog/inbox_status', '_xdog/enqueue'}
    assert 'origin' not in receiver.rows['original-id']  # Wire cannot claim human authority.


@pytest.mark.parametrize('key,value', [('inbox_id', 'other'), ('branch_id', 'other'),
                                       ('cwd', '/different'), ('session_id', 'other')])
async def test_exact_scope_mismatch_sends_nothing(receiver, key, value):
    result = await enqueue({**receiver.target, key: value}, message())
    assert result['status'] == 'unavailable'
    assert not receiver.rows
    assert '_xdog/enqueue' not in [method for method, _ in receiver.calls]


async def test_negotiation_and_private_endpoint_are_required(receiver):
    receiver.extensions = {'enqueue': 1}
    assert (await enqueue(receiver.target, message()))['status'] == 'unavailable'
    receiver.extensions = {'enqueue': True, 'inbox_status': 1}
    assert (await enqueue(receiver.target, message()))['status'] == 'unavailable'
    os.chmod(receiver.target['endpoint'], 0o666)
    assert (await enqueue(receiver.target, message()))['status'] == 'unavailable'
    assert not receiver.rows


async def test_ack_loss_verifies_original_id_and_content(receiver):
    receiver.drop_ack = True
    result = await enqueue(receiver.target, message())
    assert result['status'] == 'duplicate' and result['ack_recovered']
    sends = [params for method, params in receiver.calls if method == '_xdog/enqueue']
    assert len(sends) == 2 and sends[0] == sends[1]
    assert set(receiver.rows) == {'original-id'}


async def test_absent_after_ack_loss_is_not_automatically_retried(receiver):
    receiver.drop_before_commit = True
    assert (await enqueue(receiver.target, message()))['status'] == 'outcome-unknown'
    assert not receiver.rows
    assert len([m for m, _ in receiver.calls if m == '_xdog/enqueue']) == 1


async def test_scope_rotation_after_commit_cannot_report_success(receiver):
    receiver.rotate_after_commit = True
    result = await enqueue(receiver.target.copy(), message())
    assert result['status'] == 'outcome-unknown'
    assert set(receiver.rows) == {'original-id'}
    assert len([m for m, _ in receiver.calls if m == '_xdog/enqueue']) == 1


async def test_original_id_different_content_is_never_success(receiver):
    await enqueue(receiver.target, message())
    result = await enqueue(receiver.target, message('Different instruction'))
    assert result['status'] == 'outcome-unknown'
    assert receiver.rows['original-id']['instruction'] == message().instruction


async def test_cancel_closes_only_connection_never_sends_cancel(receiver):
    receiver.hold_enqueue = asyncio.Event()
    task = asyncio.create_task(enqueue(receiver.target, message()))
    await asyncio.wait_for(receiver.entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert 'session/cancel' not in [method for method, _ in receiver.calls]


def test_module_import_does_not_require_optional_sdk():
    code = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'acp' or name.startswith('acp.'):
        raise ImportError('SDK deliberately absent')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import xdog.agent.acp.client
'''
    completed = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
