"""Socket message grants are one-shot, owned, result-gated and default-off."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from xdog.agent.extensions.message_request import host_message

from packages.claw.tests.test_extension_message_ui import message_server


@asynccontextmanager
async def peer(config):
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(value):
        writer.write((json.dumps(value) + '\n').encode())
        await writer.drain()
    async def receive(kind):
        async with asyncio.timeout(10):
            while True:
                line = await reader.readline()
                assert line, 'Connection ended without an owned result'
                value = json.loads(line)
                if value['type'] == kind:
                    return value
    try:
        yield send, receive, writer
    finally:
        writer.close()
        await writer.wait_closed()


async def handshake(send, receive, **capabilities):
    await send({'type': 'ping', 'group_id': 'main', 'extension_session': 1, **capabilities})
    return await receive('pong')


async def request_review(send, receive, session, *, identifier='command', args=None):
    await send({'type': 'extension_control', 'request_id': identifier, 'group_id': 'main',
                'session_id': session, 'action': 'command', 'name': 'ext-messagerequest-request',
                'args': json.dumps(args or {})})
    return await receive('extension_dialog')


async def accepted_grant(send, receive, session, *, identifier='command'):
    dialog = await request_review(send, receive, session, identifier=identifier)
    assert dialog['dialog']['host_message'] == host_message('messagerequest', '/reset literal requested text')
    await send({'type': 'extension_dialog_answer', 'id': dialog['dialog']['id'], 'value': True})
    accepted = await receive('extension_dialog_accepted')
    assert accepted['id'] == dialog['dialog']['id'] and accepted['operation'] == 'message'
    result = await receive('extension_result')
    assert result['success'] is True and result['message_queued'] is True
    grant = result['message_delivery']
    return {'type': 'message', 'group_id': 'main', 'session_id': session, 'run_id': 'requested',
            'extension_message_token': grant['token'], 'extension_message_generation': grant['generation']}


@pytest.mark.parametrize('version', [None])
async def test_message_capability_is_independently_default_off(tmp_path, version):
    async with message_server(tmp_path) as (_, _, owner, config, calls):
        async with peer(config) as (send, receive, _writer):
            caps = await handshake(send, receive, extension_compaction=1, session_compaction=1,
                                   **({'extension_message': version} if version is not None else {}))
            assert caps['extension_message'] == 0 and caps['extension_compaction'] == 1
            await send({'type': 'extension_control', 'request_id': 'off', 'group_id': 'main',
                        'session_id': owner.meta.session_id, 'action': 'command',
                        'name': 'ext-messagerequest-request'})
            result = await receive('extension_result')
            assert result['success'] and not result['message_queued'] and 'message_delivery' not in result
            assert '"queued": false' in result['output'] and not calls
            caps = await handshake(send, receive, extension_message=1)
            assert caps['extension_message'] == caps['extension_compaction'] == 1
            unchanged = await handshake(send, receive, extension_navigation=0)
            assert unchanged['extension_message_generation'] == caps['extension_message_generation']
            assert unchanged['extension_message'] == 1
