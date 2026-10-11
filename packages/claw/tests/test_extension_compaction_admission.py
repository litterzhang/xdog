"""Revocation before a queued native compaction task starts still needs a receipt."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.coding.tests.test_rpc_compaction import history


@pytest.mark.parametrize('ending', ['extension', 'extension-reset', 'native', 'native-reset', 'explicit'])
async def test_same_batch_extension_revocation_before_compaction_admission_has_receipt(tmp_path, ending):
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    owner = runtime.get_or_create_session(allow_reset=False)
    history(owner)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def receive(kind):
        async with asyncio.timeout(3):
            while True:
                value = json.loads(await reader.readline())
                if value['type'] == kind:
                    return value
    try:
        writer.write((json.dumps({'type': 'ping', 'group_id': 'main', 'session_compaction': 1,
                                  'extension_compaction': 1}) + '\n').encode())
        await writer.drain()
        capabilities = await receive('pong')
        generation = capabilities['extension_compaction_generation']
        requests = [{'type': 'session_compact', 'request_id': 'queued', 'group_id': 'main',
                     'session_id': owner.meta.session_id, 'extension_compaction_generation': generation}]
        if ending == 'explicit':
            requests.append({'type': 'session_compact_cancel', 'request_id': 'queued', 'group_id': 'main',
                             'session_id': owner.meta.session_id})
        else:
            key = 'extension_compaction' if ending.startswith('extension') else 'session_compaction'
            requests.append({'type': 'ping', key: 0})
            if ending.endswith('-reset'):
                requests.append({'type': 'ping', key: 1})
        writer.write(('\n'.join(json.dumps(row) for row in requests) + '\n').encode())
        await writer.drain()
        result = await receive('session_compact_result')
        assert result['request_id'] == 'queued' and result['canceled'] and not result['success']
        assert not calls and not owner._compacting and owner._compaction_metadata is None
        async with asyncio.timeout(3):
            while server._orchestrator.is_group_running('main'):
                await asyncio.sleep(.003)
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
