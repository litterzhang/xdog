"""Manual compaction through both actual applications, selected adapters, and disk owners."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from xdog.ai.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer


def history():
    return [UserMessage(content='old work ' + 'a' * 5000),
        AssistantMessage(content=(ToolCall(id='read', name='filesystem',
                                         arguments={'action': 'read', 'path': 'old.py'}),)),
        ToolResultMessage(tool_call_id='read', tool_name='filesystem',
                          content=(TextContent(text='IMPORTANT-TOOL-RESULT'),)),
        AssistantMessage(content=(TextContent(text='old work done ' + 'b' * 1000),)),
        UserMessage(content='more work ' + 'c' * 3000),
        AssistantMessage(content=(TextContent(text='more work done ' + 'd' * 1000),)),
        UserMessage(content='recent user request'), AssistantMessage(content=(TextContent(text='recent answer'),))]


@pytest.mark.parametrize('scenario', ['legacy', 'stale', 'malformed', 'unsolicited', 'withdrawn'])
def test_claw_compaction_legacy_and_invalid_receipts_never_become_agent_input(scenario):
    ui = ChatApp('/tmp/compaction-test-unconnected.sock')
    ui._connection_ready = True
    ui._compaction_supported = scenario != 'legacy'
    ui._gateway_session_id = 'current'
    if scenario == 'legacy':
        ui._compact_command('keep decisions')
        assert ui._compact_pending is None and ui._send_queue.empty()
    else:
        ui._compact_pending = None if scenario == 'unsolicited' else ('request', 'current')
        if scenario == 'withdrawn':
            ui._compaction_supported = False
        ui._handle_response({'type': 'session_compact_result', 'request_id': 'request', 'group_id': ui.group_id,
            'session_id': 'old' if scenario == 'stale' else 'current', 'success': True, 'changed': True,
            'before': [] if scenario == 'malformed' else 8, 'after': 4})
        assert 'Compacted:' not in str(ui._chat_log.render(120))
        assert ui._gateway_session_id == 'current'
    ui._request_exit()


async def test_claw_coalesced_cancel_before_job_start_receipt_reservation_and_no_replay(tmp_path):
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'cancel.sock'), pid_file=str(tmp_path / 'cancel.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    calls = []
    def stream(*args):
        calls.append(args)
        raise AssertionError('Canceled controls must not call a model')
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def receive(kind, identifier=''):
        async with asyncio.timeout(8):
            while True:
                frame = json.loads(await reader.readline())
                if frame.get('type') == kind and (not identifier or frame.get('request_id') == identifier):
                    return frame
    try:
        writer.write((json.dumps({'type': 'ping', 'group_id': 'main', 'session_compaction': 1}) + '\n').encode())
        await writer.drain()
        assert (await receive('pong'))['session_compaction'] == 1
        request = {'type': 'session_compact', 'request_id': 'coalesced', 'group_id': 'main', 'session_id': ''}
        cancel = {**request, 'type': 'session_compact_cancel'}
        writer.write(('\n'.join(json.dumps(item) for item in (request, cancel)) + '\n').encode())
        await writer.drain()
        receipt = await receive('session_compact_result', 'coalesced')
        assert receipt['canceled'] is True and 'before summary started' in receipt['error']
        await asyncio.sleep(0)
        assert server._orchestrator.try_reserve_group('main')
        server._orchestrator.release_group_reservation('main')
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        assert (await receive('session_compact_result', 'coalesced'))['success'] is False
        writer.write((json.dumps({'type': 'ping', 'group_id': 'main', 'session_compaction': 0}) + '\n').encode())
        await writer.drain()
        assert (await receive('pong'))['session_compaction'] == 0
        writer.write((json.dumps({**request, 'request_id': 'withdrawn'}) + '\n').encode())
        await writer.drain()
        assert (await receive('session_compact_result', 'withdrawn'))['success'] is False
        assert not calls
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
