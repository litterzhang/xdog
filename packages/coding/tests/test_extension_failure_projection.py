"""Owned failure boundaries withdraw current declarations without touching other workers or policy."""
from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.types import UserInput
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode


@pytest.fixture
async def owner(tmp_path, request):
    kind, stage = request.param
    root = tmp_path / 'extensions'
    for name in ('faultytools', 'toolinspector'):
        shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions' / name, root / name)
    marker = tmp_path / 'failed-entered'
    flags = {'stage': stage, 'marker': str(marker), 'hold': stage == 'cancel'}
    contexts = []
    def stream(model, context, options=None):
        contexts.append(context)
        if context.messages[-1].role == 'user':
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_faultytools_'))
            message = AssistantMessage(content=(ToolCall(id='fault', name=tool.name, arguments={}),),
                                       stop_reason='toolUse')
        else:
            message = AssistantMessage(content=(TextContent(text='OFFLINE-FINAL'),))
        return EventStream.empty(message)
    server = task = writer = None
    if kind == 'coding':
        config = tmp_path / 'settings.json'
        config.write_text(json.dumps({'extensions': ['faultytools', 'toolinspector'], 'extension_dir': str(root),
                                      'extension_settings': {'faultytools': flags}}))
        manager = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
            session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
            settings=SettingsManager(global_path=config), working_dir=tmp_path, tool_registry=None, bash=None,
            permissions=PermissionManager('allow-all'))  # Explicit offline test opt-in, not a changed host default.
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'fault.sock'), pid_file=str(tmp_path / 'fault.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('faultytools', 'toolinspector'),
            extension_dir=str(root), extension_settings={'faultytools': flags}, extension_allow_tools=('faultytools',))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, stream)):
            await server.start()
        runtime = server._orchestrator._runtimes['main']
        session = runtime.get_or_create_session(allow_reset=False)
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    await session.initialize_extensions()
    if kind == 'claw':
        await wait(lambda: ui._connection_ready)
    try:
        yield kind, stage, session, ui, server, contexts, marker, wait
    finally:
        ui._request_exit()
        if kind == 'coding':
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            session.dispose()
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()


@pytest.mark.parametrize('owner', [(kind, stage) for kind in ('coding', 'claw')
    for stage in ('command', 'cancel', 'tool', 'pre', 'post', 'context', 'input', 'event')], indirect=True)
async def test_actual_failed_callback_withdraws_agent_inventory_ui_and_retains_other_worker_and_guards(owner):
    kind, stage, session, ui, server, contexts, marker, wait = owner
    parent_agent = session.agent
    old = next(tool for tool in session.agent.state.tools if tool.name.startswith('ext_faultytools_'))
    healthy = session.extensions._manager._clients['toolinspector'].process
    own_thread = threading.get_ident()
    projected_on = []
    set_tools = session.agent.set_tools
    def observe(tools):
        projected_on.append(threading.get_ident())
        set_tools(tools)
    # SDK operations run here; UI commands run on their own explicit command loop.
    session.agent.set_tools = observe
    ui._editor.set_text('FAILURE-RETAINED-DRAFT')
    if stage in {'command', 'cancel'}:
        if kind == 'coding':
            ui._handle_slash_command('ext-faultytools-fail', '')
        else:
            ui._extension_command('ext-faultytools-fail', '')
        if stage == 'cancel':
            await wait(marker.exists)
            if kind == 'coding':
                ui._handle_escape()
            else:
                assert ui._extension_cancel_supported
                ui._handle_escape()
        await wait(lambda: ui._command is None if kind == 'coding' else ui._extension_pending is None)
    else:
        try:
            if kind == 'coding':
                await session.send_message('offline failure boundary')
            else:
                await session.run_turn(UserInput(group_id='main', content='offline failure boundary'))
        except Exception:
            assert stage in {'event', 'context', 'input'}
    assert session.agent is parent_agent
    assert not any(tool.name.startswith('ext_faultytools_') for tool in session.agent.state.tools)
    assert not any(tool.name.startswith('ext_faultytools_') for tool in session.extensions.tools())
    assert old.name in session.extensions.aliases  # Retained guard identity is not a live advertisement.
    assert healthy.returncode is None
    assert any(tool.name.startswith('ext_toolinspector_') for tool in session.agent.state.tools)
    assert session.extensions.snapshot()['extensions'][0]['state'] == 'failed'
    with pytest.raises(ValueError, match='registration changed'):
        checked = old.validate_arguments({})
        if asyncio.iscoroutine(checked):
            await checked
    inventory = session._extension_tools()
    assert not any(row['name'].startswith('ext_faultytools_') for row in inventory['tools'])
    inspected = json.loads((await session.extensions.command('ext-toolinspector-inspect', '')
                           ).removeprefix('TOOL-INVENTORY '))
    assert old.name not in inspected['names']
    if stage == 'post':
        assert 'ORIGINAL-ACTUAL-RESULT' in str(session.agent.state.messages)  # Post error cannot mask actual result.
    if stage in {'command', 'cancel'}:
        assert not contexts and ui._editor.get_text() == 'FAILURE-RETAINED-DRAFT'
        if stage == 'command':
            assert not any(name.startswith('ext-faultytools-') for name in ui._extension_snapshot['commands'])
    else:
        assert projected_on and set(projected_on) == {own_thread}
    if kind == 'coding':
        assert session.permissions.mode == 'allow-all'


@pytest.mark.parametrize('owner', [(kind, 'child-context') for kind in ('coding', 'claw')], indirect=True)
async def test_actual_child_context_failure_retains_subset_and_does_not_disable_healthy_parent(owner):
    from xdog.agent import AgentTool, AgentToolResult

    kind, _, session, _, _, contexts, _, _ = owner
    async def native(*args):
        return AgentToolResult(content=(TextContent(text='native read-only probe'),))
    probe = AgentTool(name='current_time', description='Offline read-only probe',
                      parameters={'type': 'object'}, execute=native)
    if not any(tool.name == 'current_time' for tool in session.agent.state.tools):
        session.agent.set_tools((*session.agent.state.tools, probe))
    parent_process = session.extensions._manager._clients['faultytools'].process
    built = []
    factory = session.children.supervisor.factory
    async def captured(spec, publish):
        agent = await factory(spec, publish)
        built.append(agent)
        return agent
    session.children.supervisor.factory = captured
    identifier = await session.children.supervisor.start('read-only child projection',
                                                       config={'tools': ['current_time']})
    await session.children.supervisor.control('wait', identifier)
    record = session.children.supervisor.capture_source(identifier)
    assert len(built) == 1
    child = built[0]
    assert record.status == 'failed' and not record.settling
    assert [tool.name for tool in child.state.tools] == ['current_time']
    assert session.extensions.snapshot()['extensions'][0]['state'] == 'active'
    assert parent_process.returncode is None and not contexts
    assert any(tool.name.startswith('ext_faultytools_') for tool in session.agent.state.tools)
