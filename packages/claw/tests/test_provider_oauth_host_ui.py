"""Native Claw provider authentication: cold explicit ownership, private code, no model permission."""
from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.agent.tests.test_extension_provider_oauth import PROVIDER
from packages.claw.tests.test_extension_message_gateway import handshake, peer


@asynccontextmanager
async def auth_server(tmp_path, *, settings=None):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/provideroauth', root / 'provideroauth')
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('provideroauth',), extension_dir=str(root),
        extension_settings={'provideroauth': settings or {}})
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    assert runtime._active_session is None and not runtime.get_new_session_id()
    try:
        yield server, runtime, config, calls
    finally:
        await asyncio.wait_for(server.stop(), 10)


@pytest.mark.parametrize('version', [None, True, '1', 0])
async def test_provider_auth_default_off_does_not_borrow_other_extension_grants(tmp_path, version):
    async with auth_server(tmp_path) as (_, runtime, config, calls):
        async with peer(config) as (send, receive, _writer):
            caps = await handshake(send, receive, extension_message=1, extension_editor_write=1,
                **({'extension_provider_auth': version} if version is not None else {}))
            assert caps['extension_provider_auth'] == 0
            await send({'type': 'extension_control', 'request_id': 'off', 'group_id': 'main', 'session_id': '',
                        'action': 'auth', 'args': 'login ' + PROVIDER})
            result = await receive('extension_result')
            assert not result['success'] and not calls and runtime._active_session is None


@pytest.mark.parametrize('reenable', [False, True])
async def test_auth_withdrawal_before_coroutine_admission_has_one_canceled_receipt(tmp_path, monkeypatch, reenable):
    async with auth_server(tmp_path) as (server, runtime, config, calls):
        results = []
        original = server._write_response
        async def record(writer, data):
            if data.get('type') == 'extension_result':
                results.append(data)
            await original(writer, data)
        monkeypatch.setattr(server, '_write_response', record)
        async with peer(config) as (send, receive, writer):
            await handshake(send, receive, extension_provider_auth=1)
            batch = [{'type': 'extension_control', 'request_id': 'pre-admission', 'group_id': 'main', 'session_id': '',
                      'action': 'auth', 'args': 'login ' + PROVIDER},
                     {'type': 'ping', 'extension_provider_auth': 0}]
            if reenable:
                batch.append({'type': 'ping', 'extension_provider_auth': 1})
            writer.write(('\n'.join(json.dumps(row) for row in batch) + '\n').encode())
            await writer.drain()
            async with asyncio.timeout(2):
                result = await receive('extension_result')
            assert result['canceled'] and not result['success'] and result['request_id'] == 'pre-admission'
            async with asyncio.timeout(3):
                while server._orchestrator.is_group_running('main'):
                    await asyncio.sleep(.003)
            assert len(results) == 1 and not calls and runtime._active_session is None
