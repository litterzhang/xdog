from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.components.extension_custom import ExtensionCustom
from xdog.tui.components.extension_dialog import ExtensionDialog
from xdog.tui.keys import KeyEvent


@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('mode', ['', 'custom', 'full'])
async def test_claw_nested_stack_connection_leases_restore_same_parent_and_draft(tmp_path, child, mode):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if context.messages[-1].role == 'user':
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_nested_'))
            result = AssistantMessage(content=(ToolCall(id='nested', name=tool.name, arguments={'mode': mode}),))
        else:
            result = AssistantMessage(content=(TextContent(text='done'),))
        return EventStream.empty(result)
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'nested.sock'), pid_file=str(tmp_path / 'nested.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('nested',), extension_dir=str(root),
        extension_allow_tools=('nested',))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    if mode == 'full':
        ui._tui._running = True
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    other_reader, other_writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                if ui._extension_dialog_panel is not None:
                    ui._layout.set_height(30)
                    if mode == 'full':
                        ui._layout.render(80)
                    ui._extension_dialog_panel.render(80)
                await asyncio.sleep(0.003)
    try:
        await wait(lambda: ui._connection_ready)
        assert ui._extension_custom_nested_supported
        ui._editor.set_text('original draft')
        ui._editor._cursor = 3
        if child:
            ui._agents_command('start --inherit nested question')
        else:
            ui._extension_command('ext-nested-view', mode)
        await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionCustom)
                   and ui._extension_dialog_panel._painted > 0)
        parent = ui._extension_dialog_panel
        parent.handle_input(KeyEvent('n'))
        await wait(lambda: ui._extension_dialog_panel is not parent)
        nested = ui._extension_dialog_panel
        assert nested.dialog['parent_id'] == parent.dialog['id'] and not parent.focused and parent._painted == -1
        forged = {'type': 'child_extension_dialog_answer' if child else 'extension_dialog_answer',
                  'id': nested.dialog['id'], 'value': None, 'group_id': 'main',
                  'session_id': ui._gateway_session_id}
        if child:
            forged.update(child_id=nested.dialog['child_id'], child_owner=nested.dialog['child_owner'])
        other_writer.write((json.dumps(forged) + '\n').encode())
        await other_writer.drain()
        reply = json.loads(await asyncio.wait_for(other_reader.readline(), 3))
        assert not reply['accepted']
        if mode == 'custom':
            assert isinstance(nested, ExtensionCustom)
            await wait(lambda: nested._painted > 0)
            nested.handle_input(KeyEvent('n'))
            await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionDialog))
            grandchild = ui._extension_dialog_panel
            assert grandchild.dialog['parent_id'] == nested.dialog['id']
            grandchild._input.set_text('grand')
            grandchild.render(80)
            grandchild.handle_input(KeyEvent('enter'))
            await wait(lambda: ui._extension_dialog_panel is nested and nested._painted > 0)
            nested.handle_input(KeyEvent('enter'))
        else:
            assert isinstance(nested, ExtensionDialog)
            nested._input.set_text('answer')
            nested.render(80)
            nested.handle_input(KeyEvent('enter'))
            await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionDialog)
                       and ui._extension_dialog_panel.dialog['kind'] == 'confirm')
            confirm = ui._extension_dialog_panel
            confirm.handle_input(KeyEvent('down'))
            confirm.render(80)
            confirm.handle_input(KeyEvent('enter'))
        await wait(lambda: ui._extension_dialog_panel is parent and parent._painted > 0)
        assert parent.focused and ui._extension_stack.panels[parent.dialog['id']] is parent
        assert parent.fullscreen == (mode == 'full')
        parent.handle_input(KeyEvent('enter'))
        if child:
            session = server._orchestrator._runtimes['main']._active_session
            await wait(lambda: not session.children.supervisor.active and not ui._children_pending)
            record = await session.children.supervisor.control('wait', nested.dialog['child_id'])
            assert record['status'] == 'completed' and len(calls) == 2
        else:
            await wait(lambda: ui._extension_pending is None)
            assert not calls
        assert ui._tui._focused is ui._editor and ui._editor.get_text() == 'original draft'
        assert ui._editor._cursor == 3 and not ui._extension_stack.panels
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        other_writer.close()
        await asyncio.gather(writer.wait_closed(), other_writer.wait_closed())
        await server.stop()


@pytest.mark.parametrize('withdraw', [False, True])
async def test_nested_capability_refusal_or_withdrawal_cancels_only_child_question(tmp_path, withdraw):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    def stream(*args):
        raise AssertionError('No model call for a nested extension command')
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'nested.sock'), pid_file=str(tmp_path / 'nested.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('nested',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def read(kind):
        async with asyncio.timeout(6):
            while True:
                value = json.loads(await reader.readline())
                if value['type'] == kind:
                    return value
    try:
        writer.write((json.dumps({'type': 'ping', 'extension_custom': 1,
                                 'extension_custom_nested': 1 if withdraw else 0}) + '\n').encode())
        await writer.drain()
        await read('pong')
        writer.write(b'{"type":"extension_control","request_id":"view","group_id":"main",'
                     b'"session_id":"","action":"command","name":"ext-nested-view","args":""}\n')
        await writer.drain()
        notice = await read('extension_dialog')
        parent, session_id = notice['dialog']['id'], notice['session_id']
        request = {'type': 'extension_custom_request', 'group_id': 'main', 'session_id': session_id, 'id': parent,
                   'event': {'kind': 'resize', 'width': 60, 'height': 5, 'painted': -1, 'generation': 1}}
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        frame = await read('extension_custom_frame')
        request['event'].update(kind='key', key='n', ctrl=False, alt=False, shift=False, painted=frame['revision'])
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        if withdraw:
            nested = await read('extension_dialog')
            assert nested['dialog']['parent_id'] == parent
            writer.write(b'{"type":"ping","extension_custom_nested":0}\n')
            await writer.drain()
        # Both sequential nested questions are refused; parent resumes, not adopted
        # as a standalone question and not canceled by the unsupported child surface.
        frame = await read('extension_custom_frame')
        assert 'null' in str(frame['lines'])
        request['event'] = {'kind': 'resize', 'width': 60, 'height': 5, 'painted': -1, 'generation': 2}
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        frame = await read('extension_custom_frame')
        request['event'].update(kind='key', key='enter', ctrl=False, alt=False, shift=False, painted=frame['revision'])
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        result = await read('extension_result')
        assert result['success'] and json.loads(result['output'])['values'] == [None, False]
        assert json.loads(result['output'])['calls'] == 1
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
