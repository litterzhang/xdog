from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_actual_live_tool_catalog_replacement_disposal_schema_policy_and_same_worker(tmp_path, kind):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if context.messages[-1].role == 'user':
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_toolmock_target_'))
            value = 7 if tool.parameters['properties']['value']['type'] == 'integer' else 'first'
            message = AssistantMessage(content=(ToolCall(id='target', name=tool.name, arguments={'value': value}),))
        else:
            message = AssistantMessage(content=(TextContent(text='TOOL-TURN-DONE'),))
        return EventStream.empty(message)
    server = None
    task = writer = None
    if kind == 'coding':
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'extensions': ['toolmock'], 'extension_dir': str(root)}))
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
            session_data=store.create_session(working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
            permissions=PermissionManager('ask'))
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'tools.sock'), pid_file=str(tmp_path / 'tools.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('toolmock',), extension_dir=str(root),
            extension_allow_tools=('toolmock',))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
            await server.start()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(10):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    async def command(name):
        if kind == 'coding':
            ui._handle_slash_command('ext-toolmock-' + name, '')
            await wait(lambda: ui._command is None)
        else:
            ui._extension_command('ext-toolmock-' + name, '')
            await wait(lambda: ui._extension_pending is None)
    async def prompt(count):
        ui._handle_submit('use actual current tool')
        if kind == 'coding':
            await wait(lambda: ui._permission_prompt is not None)
            assert await session.extensions.command('ext-toolmock-count', '') == str(count // 2 - 1)
            ui._permission_prompt.render(80)
            ui._permission_prompt.handle_input(KeyEvent('enter'))
        await wait(lambda: len(calls) == count and (not ui._worker_active and not ui._is_busy if kind == 'coding'
                                                  else ui._active_run_id is None and not ui._waiting))
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
        await command('enable')
        if kind == 'claw':
            session = server._orchestrator._runtimes['main']._active_session
        process = session.extensions._manager._clients['toolmock'].process
        target = next(tool for tool in session.extensions.tools() if '/target' in tool.label)
        assert target.parameters['properties']['value']['type'] == 'string'
        await prompt(2)
        assert await session.extensions.command('ext-toolmock-count', '') == '1'
        await command('replace')
        target = next(tool for tool in session.extensions.tools() if '/target' in tool.label)
        assert target.parameters['properties']['value']['type'] == 'integer'
        await prompt(4)
        assert await session.extensions.command('ext-toolmock-count', '') == '2'
        await command('disable')
        assert not any('/target' in tool.label for tool in session.extensions.tools())
        assert not any(tool.name.startswith('ext_toolmock_target_') for tool in session.agent.state.tools)
        assert session.extensions._manager._clients['toolmock'].process is process
        body = '\n'.join(ui._chat_log.render(200))
        assert 'LIVE-TOOL-ORIGINAL-first' in body and 'LIVE-TOOL-REPLACED-7' in body
        ui._editor.set_text('kept live tool draft')
        assert ui._tui._focused is ui._editor and ui._extension_dialog_panel is None
        if kind == 'coding':
            assert session.permissions.mode == 'ask'
    finally:
        ui._request_exit()
        if kind == 'claw':
            session = server._orchestrator._runtimes['main']._active_session
        if session is not None:
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
        if kind == 'coding':
            session.dispose()
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()


async def test_coding_registration_change_while_approval_waits_cannot_execute_replacement(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['toolmock'], 'extension_dir': str(root)}))
    store = SessionManager(tmp_path / 'sessions')
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if len(calls) == 1:
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_toolmock_target_'))
            message = AssistantMessage(content=(ToolCall(id='old', name=tool.name, arguments={'value': 'old'}),))
        else:
            message = AssistantMessage(content=(TextContent(text='refused stale executor'),))
        return EventStream.empty(message)
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
        permissions=PermissionManager('ask'))
    ui = InteractiveMode(session)
    async def wait(predicate):
        async with asyncio.timeout(10):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        await session.extension_command('ext-toolmock-enable')
        ui._handle_submit('ask for original tool')
        await wait(lambda: ui._permission_prompt is not None)
        # A trusted same-owner registration may change while an already admitted
        # approval is outstanding. The approval cannot authorize that replacement.
        await session.extensions.command('ext-toolmock-replace', '')
        ui._permission_prompt.render(80)
        ui._permission_prompt.handle_input(KeyEvent('enter'))
        await wait(lambda: len(calls) == 2 and not ui._worker_active and not ui._is_busy)
        assert await session.extensions.command('ext-toolmock-count', '') == '0'
        result = next(message for message in calls[-1].messages if message.role == 'toolResult')
        assert result.is_error and 'registration changed' in str(result.content)
        assert session.extensions.snapshot()['extensions'][0]['state'] == 'active'
        assert session.permissions.mode == 'ask'
    finally:
        ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        session.dispose()


@pytest.mark.parametrize('child', [False, True])
async def test_actual_coding_parent_and_child_next_request_projects_tool_created_inside_callback(tmp_path, child):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['toolmock'], 'extension_dir': str(root)}))
    store = SessionManager(tmp_path / 'sessions')
    contexts = []
    def stream(model, context, options):
        contexts.append(context)
        names = {tool.name for tool in context.tools or ()}
        if len(contexts) == 1:
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_toolmock_upgrade_'))
            message = AssistantMessage(content=(ToolCall(id='upgrade', name=tool.name, arguments={}),))
        elif len(contexts) == 2:
            assert not any(name.startswith('ext_toolmock_upgrade_') for name in names)
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_toolmock_target_'))
            assert tool.parameters['properties']['value']['type'] == 'integer'
            message = AssistantMessage(content=(ToolCall(id='new', name=tool.name, arguments={'value': 7}),))
        else:
            message = AssistantMessage(content=(TextContent(text='same actual loop finished'),))
        return EventStream.empty(message)
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
        permissions=PermissionManager('allow-all'))
    try:
        await session.extension_command('ext-toolmock-chain')
        if child:
            # Independent workers do not inherit parent command side effects. Seed
            # this child via its own startup callback in the owned copied fixture.
            source = root / 'toolmock/main.py'
            startup = "\n    api.on('session_start', lambda context: chain('', context))\n"
            source.write_text(source.read_text() + startup)
            identifier = await session.children.supervisor.start('same-loop child', access='inherit')
            result = await session.children.supervisor.control('wait', identifier)
            assert result['status'] == 'completed'
        else:
            await session.send_message('same-loop parent')
        assert len(contexts) == 3
        assert session.permissions.mode == 'allow-all'
    finally:
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        session.dispose()
