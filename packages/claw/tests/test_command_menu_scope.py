"""Actual gateway callback menu replies keep independent connection/session/generation ownership."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent.extensions.command_catalog import page
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.claw.tests.test_extension_message_gateway import peer


@pytest.mark.parametrize('ending', ['read', 'old-client', 'foreign', 'withdraw', 'source'])
async def test_original_model_tool_menu_is_independent_readonly_and_revocable(tmp_path, ending):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/menureader', root / 'menureader')
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if context.messages[-1].role == 'user':
            tool = next(tool for tool in context.tools if 'menureader' in tool.name)
            message = AssistantMessage(content=(ToolCall(id='menu', name=tool.name, arguments={}),),
                                       stop_reason='toolUse')
        else:
            message = AssistantMessage(content=(TextContent(text='MENU-TURN-DONE'),))
        return EventStream.empty(message)
    config = ClawConfig(model='offline', data_dir=str(tmp_path / 'data'), socket_path=str(tmp_path / 'socket'),
        pid_file=str(tmp_path / 'pid'), groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('menureader',), extension_dir=str(root), extension_allow_tools=('menureader',))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline', stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    owner = runtime.get_or_create_session(allow_reset=False)
    try:
        async with peer(config) as (send, receive, _writer):
            await send({'type': 'ping', 'group_id': 'main', 'extension_command_catalog': int(ending != 'old-client')})
            caps = await receive('pong')
            await send({'type': 'message', 'group_id': 'main', 'session_id': owner.meta.session_id,
                        'run_id': 'owned-menu-turn', 'content': 'Original model input'})
            if ending != 'old-client':
                event = await receive('extension_dialog')
                dialog = event['dialog']
                assert dialog['command_menu_generation'] == caps['extension_command_catalog_generation']
                assert dialog['command_menu']['method'] == 'tools/call'
                result = page({'/peer-only': 'Only this client knows this menu'}, dialog,
                              session_id=owner.meta.session_id, owner='actual-client')
                reply = {'type': 'extension_dialog_answer', 'id': dialog['id'], 'group_id': 'main',
                         'session_id': owner.meta.session_id, 'value': result}
                if ending == 'foreign':
                    async with peer(config) as (other_send, other_receive, _other):
                        await other_send(reply)
                        assert (await other_receive('extension_dialog_ack'))['accepted'] is False
                    await send({**reply, 'value': {**result, 'session_id': 'foreign'}})
                    assert (await receive('extension_dialog_ack'))['accepted'] is False
                    await send({**reply, 'group_id': 'foreign'})
                    assert (await receive('extension_dialog_ack'))['accepted'] is False
                if ending == 'withdraw':
                    await send({'type': 'ping', 'extension_command_catalog': 0})
                    await receive('pong')
                    await send({'type': 'ping', 'extension_command_catalog': 1})
                    assert (await receive('pong'))['extension_command_catalog_generation'] > caps[
                        'extension_command_catalog_generation']
                    await send(reply)
                    assert (await receive('extension_dialog_ack'))['accepted'] is False
                else:
                    if ending == 'source':
                        runtime._source_revision += 1
                    await send(reply)
                    assert (await receive('extension_dialog_ack'))['accepted'] is True
                    if ending in {'read', 'foreign'}:
                        second = await receive('extension_dialog')
                        await send({**reply, 'id': second['dialog']['id'], 'value': page(
                            {'/peer-only': 'Only this client knows this menu'}, second['dialog'],
                            session_id=owner.meta.session_id, owner='actual-client')})
                        assert (await receive('extension_dialog_ack'))['accepted'] is True
            async with asyncio.timeout(8):
                while len(calls) < 2 or server._orchestrator._queue.is_running('main'):
                    await asyncio.sleep(.003)
            row = owner.extensions.entries.for_extension('menureader')[-1]['data']
            assert row['available'] == (ending in {'read', 'foreign'})
            assert row['names'] == (['/peer-only'] if row['available'] else []) and row['executed'] == 0
            assert len(calls) == 2 and owner.extensions.command_menu_handler is None
    finally:
        await server.stop()
