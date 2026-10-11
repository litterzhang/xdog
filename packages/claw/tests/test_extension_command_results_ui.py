from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

FIXTURE = Path(__file__).parents[2] / 'agent/tests/fixtures/extensions'


@pytest.mark.parametrize('viewer', ['new', 'old'])
async def test_live_claw_rich_command_payload_negotiation_and_no_agent_state_mutation(tmp_path, viewer):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'command.sock'), pid_file=str(tmp_path / 'command.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('rich',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    task = None
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)
    try:
        if viewer == 'new':
            task = asyncio.create_task(ui._run_connection(reader, writer))
            await wait(lambda: ui._connection_ready)
            assert ui._extension_command_results_supported
            ui._editor.set_text('parent draft')
            ui._extension_command('ext-rich-show', '')
            await wait(lambda: ui._extension_pending is None)
            artifacts = [child for child in ui._chat_log.children if child.__class__.__name__ == 'ExtensionCommand']
            assert len(artifacts) == 1 and len(artifacts[0]._images) == 1
            assert artifacts[0].result['details']['enabled'] is False
            ui._open_transcript()
            await wait(lambda: ui._source_records is not None)
            assert any(record.role == 'system' and '[false,0,null]' in record.body
                       for record in ui._full_transcript_records())
            assert ui._editor.get_text() == 'parent draft'
        else:
            writer.write(b'{"type":"ping","group_id":"main"}\n')
            await writer.drain()
            state = json.loads(await reader.readline())
            writer.write((json.dumps({'type': 'extension_control', 'request_id': 'old', 'group_id': 'main',
                'session_id': state.get('session_id', ''), 'action': 'command', 'name': 'ext-rich-show', 'args': ''})
                          + '\n').encode())
            await writer.drain()
            async with asyncio.timeout(8):
                while True:
                    reply = json.loads(await reader.readline())
                    if reply['type'] == 'extension_result':
                        break
            assert reply['success'] and 'command_content' not in reply
            assert 'updated rich-command viewer required' in reply['output']
            assert 'iVBORw0' not in json.dumps(reply)
        assert not calls
        session = server._orchestrator._runtimes['main']._active_session
        assert not session.agent.state.messages
    finally:
        ui._request_exit()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


def test_claw_stale_or_unnegotiated_command_artifact_never_becomes_raw_prompt_or_base64():
    from xdog.agent.extensions.command_result import ExtensionCommandResult
    from xdog.agent.extensions.tool_result import make_result
    ui = ChatApp('/tmp/unused-rich-command.sock')
    artifact = ExtensionCommandResult('rich', 'ext-rich-show', make_result(structured=[False, None]))
    try:
        ui._gateway_session_id = 'current'
        ui._editor.set_text('parent draft')
        ui._extension_pending = 'request', 'old-session'
        ui._handle_response({'type': 'extension_result', 'request_id': 'request', 'group_id': 'main',
            'success': True, 'session_id': 'old-session', 'command_content': artifact.to_dict()})
        assert not any(child.__class__.__name__ == 'ExtensionCommand' for child in ui._chat_log.children)
        ui._extension_pending = 'next', 'current'
        ui._extension_command_results_supported = False
        ui._handle_response({'type': 'extension_result', 'request_id': 'next', 'group_id': 'main',
            'success': True, 'session_id': 'current', 'command_content': artifact.to_dict()})
        assert not any(child.__class__.__name__ == 'ExtensionCommand' for child in ui._chat_log.children)
        assert ui._editor.get_text() == 'parent draft' and ui._send_queue.empty()
    finally:
        ui._request_exit()
