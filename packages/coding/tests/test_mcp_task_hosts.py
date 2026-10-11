"""Both actual native hosts explicitly arm tasks; painted consent still owns result release."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent

from packages.agent.tests.test_mcp_sampling_executor import Adapter
from packages.coding.tests.test_mcp_elicitation_ui import paint as paint_form
from packages.coding.tests.test_mcp_sampling_ui import paint as paint_sampling


def task_config(tmp_path, family):
    peer = Path(__file__).resolve().parents[2] / 'agent/tests/fixtures/mcp_task_peer.py'
    log = tmp_path / 'task-frames'
    return {'owned': {'command': sys.executable, 'args': [str(peer), str(log), family], 'timeout': 30}}, log


async def claw_setup(tmp_path, family):
    servers, log = task_config(tmp_path, family)
    adapter = Adapter()
    config = ClawConfig(model='explicit-model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),), mcp_servers=servers)
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('explicit-model', adapter)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    session = runtime.get_or_create_session(allow_reset=False)
    return server, config, runtime, session, adapter, log


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('family', ['sampling', 'elicitation'])
@pytest.mark.parametrize('end', ['release', 'close'])
async def test_native_tasks_explicit_arm_full_reviews_original_session_and_cleanup(tmp_path, kind, family, end):
    server, connection, writer = None, None, None
    if kind == 'coding':
        configs, log = task_config(tmp_path, family)
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'mcp_servers': configs}))
        sessions, adapter = SessionManager(tmp_path / 'sessions'), Adapter()
        session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='explicit-model')),
            session_data=sessions.create_session(model='explicit-model', working_dir=tmp_path),
            session_manager=sessions, settings=SettingsManager(global_path=settings), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('deny'))
        manager, ui = session.mcp, InteractiveMode(session)
    else:
        server, config, runtime, session, adapter, log = await claw_setup(tmp_path, family)
        manager, ui = runtime.mcp, ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        connection = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    def frames():
        return [json.loads(raw) for raw in log.read_text().splitlines()]
    def review():
        return ui._mcp_panel.sampling_review if family == 'sampling' else ui._mcp_panel.elicitation_review
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready and ui._mcp_sampling_supported and ui._mcp_elicitation_supported)
        ui._editor.set_text('UNSENT PARENT TASK DRAFT')
        ui._open_mcp()
        await wait(lambda: not ui._mcp_panel._busy)
        panel = ui._mcp_panel
        assert not log.exists() and not manager._sampling and not manager._elicitation
        panel.handle_input(KeyEvent('j' if family == 'sampling' else 'k'))
        await wait(lambda: not panel._busy)
        assert 'tasks armed' in panel._message and not log.exists() and not adapter.calls
        panel.handle_input(KeyEvent('c'))
        await wait(lambda: review() is not None and any(row.get('id') == 'create' for row in frames()))
        client = manager._clients['owned']
        registry = client.reverse.tasks
        assert registry is not None
        initial = next(row for row in frames() if row.get('method') == 'initialize')
        assert initial['params']['capabilities']['tasks']['requests'] == {
            family: {'createMessage' if family == 'sampling' else 'create': {}}}
        if family == 'sampling':
            assert initial['params']['capabilities']['sampling'] == {'tools': {}, 'context': {}}
        created = next(row for row in frames() if row.get('id') == 'create')['result']['task']
        assert registry._tasks[created['taskId']].status == 'input_required'
        assert review().event['body']['params']['task'] == {'ttl': 60000, 'pollInterval': 500}
        first = review()
        first.handle_input(KeyEvent('enter'))
        assert not adapter.calls and not any(row.get('id') == 'retrieve' for row in frames())
        if family == 'sampling':
            first.editor.set_text(json.dumps({'model': 'explicit-model', 'max_tokens': 10,
                                             'context': 'none', 'tools': []}))
            first.handle_input(KeyEvent('enter'))
            paint_sampling(first)
            first.handle_input(KeyEvent('enter'))
        else:
            paint_form(first)
            first.handle_input(KeyEvent('enter'))
            assert first._mode == 'form'
            first.editor.set_text('{"name":"PRIVATE TASK FORM"}')
            first.handle_input(KeyEvent('enter'))
            paint_form(first)
            first.handle_input(KeyEvent('enter'))
        await wait(lambda: review() is not None and review().event['phase'] ==
                   ('output' if family == 'sampling' else 'response'))
        assert not any(row.get('id') == 'retrieve' for row in frames())
        assert registry._tasks[created['taskId']].status == 'input_required'
        last = review()
        if end == 'release':
            (paint_sampling if family == 'sampling' else paint_form)(last)
            last.handle_input(KeyEvent('enter'))
            await wait(lambda: any(row.get('id') == 'retrieve' for row in frames()))
            returned = next(row for row in frames() if row.get('id') == 'retrieve')['result']
            assert returned['_meta']['io.modelcontextprotocol/related-task'] == {'taskId': created['taskId']}
            assert (returned['content']['text'] == 'MODEL OUTPUT' if family == 'sampling'
                    else returned['content'] == {'name': 'PRIVATE TASK FORM'})
        await wait(lambda: any(row.get('method') == 'notifications/tasks/status' for row in frames()))
        notices = [row for row in frames() if row.get('method') == 'notifications/tasks/status']
        assert all(row['params']['taskId'] == created['taskId'] and 'id' not in row for row in notices)
        assert 'PRIVATE' not in json.dumps(notices)
        ui._close_mcp()
        await wait(lambda: not manager._sampling and not manager._elicitation and not registry._tasks
                   and not client.reverse.status._jobs)
        assert len(adapter.calls) == (1 if family == 'sampling' else 0)
        assert ui._editor.get_text() == 'UNSENT PARENT TASK DRAFT'
        assert not session.agent.messages
        if end == 'close':
            assert not any(row.get('id') == 'retrieve' and 'result' in row for row in frames())
        if kind == 'coding':
            assert session.permissions.mode == 'deny' and not session._auth_changing
        else:
            assert not server._orchestrator.is_group_running('main')
    finally:
        ui._request_exit()
        if kind == 'coding':
            await manager.aclose()
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            session.dispose()
        else:
            connection.cancel()
            await asyncio.gather(connection, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()


@pytest.mark.parametrize('family', ['sampling', 'elicitation'])
@pytest.mark.parametrize('tasks', [1, 'true', None])
async def test_claw_task_control_requires_explicit_boolean_before_registration(tmp_path, family, tasks):
    server, config, runtime, session, adapter, _ = await claw_setup(tmp_path, family)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    from packages.claw.tests.test_mcp_sampling_gateway import receive
    async def send(packet, kind):
        writer.write((json.dumps(packet) + '\n').encode())
        await writer.drain()
        return await receive(reader, kind)
    try:
        await send({'type': 'ping', 'group_id': 'main', 'mcp_sampling': 1, 'mcp_elicitation': 1}, 'pong')
        result = await send({'type': f'mcp_{family}_control', 'group_id': 'main',
            'session_id': session.meta.session_id, 'view': 'a' * 32, 'server': 'owned',
            'request_id': 'invalid-task', 'action': 'enable', 'tasks': tasks}, f'mcp_{family}_result')
        assert not result['success'] and not runtime.mcp._sampling and not runtime.mcp._elicitation
        assert not adapter.calls
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('family', ['sampling', 'elicitation'])
async def test_native_task_expires_without_operator_poll_or_approval_and_restores_view(tmp_path, kind, family):
    server, connection, writer = None, None, None
    if kind == 'coding':
        configs, log = task_config(tmp_path, family)
        configs['owned']['args'].append('600')
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'mcp_servers': configs}))
        sessions, adapter = SessionManager(tmp_path / 'sessions'), Adapter()
        session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='explicit-model')),
            session_data=sessions.create_session(model='explicit-model', working_dir=tmp_path),
            session_manager=sessions, settings=SettingsManager(global_path=settings), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('deny'))
        manager, ui = session.mcp, InteractiveMode(session)
    else:
        server, config, runtime, session, adapter, log = await claw_setup(tmp_path, family)
        # The configured immutable args are replaced before registration/connection;
        # no user's peer or running test process is edited/restarted.
        from dataclasses import replace
        original = runtime.mcp.configs['owned']
        runtime.mcp.configs['owned'] = replace(original, command=(*original.command, '600'))
        manager, ui = runtime.mcp, ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        connection = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    def frames():
        return [json.loads(raw) for raw in log.read_text().splitlines()]
    def review():
        return ui._mcp_panel.sampling_review if family == 'sampling' else ui._mcp_panel.elicitation_review
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready and ui._mcp_sampling_supported and ui._mcp_elicitation_supported)
        ui._editor.set_text('PARENT UNAPPROVED TTL DRAFT')
        ui._open_mcp()
        await wait(lambda: not ui._mcp_panel._busy)
        ui._mcp_panel.handle_input(KeyEvent('j' if family == 'sampling' else 'k'))
        await wait(lambda: not ui._mcp_panel._busy)
        ui._mcp_panel.handle_input(KeyEvent('c'))
        await wait(lambda: review() is not None)
        registry = manager._clients['owned'].reverse.tasks
        assert registry is not None and not adapter.calls
        # Do not answer, read state/list or sweep; only wait for actual deadline
        # cleanup. Native UI polling renders events, not registry lifecycle calls.
        await wait(lambda: review() is None and not registry._tasks and not registry._expiring)
        await wait(lambda: any(row.get('id') == 'retrieve' for row in frames()))
        assert next(row for row in frames() if row.get('id') == 'retrieve')['error']['code'] == -32000
        assert registry._retained_bytes == 0 and not adapter.calls and not session.agent.messages
        assert ui._editor.get_text() == 'PARENT UNAPPROVED TTL DRAFT'
        assert not ui._mcp_panel._busy
        ui._close_mcp()
        if kind == 'coding':
            assert session.permissions.mode == 'deny' and not session._auth_changing
        else:
            assert not server._orchestrator.is_group_running('main')
    finally:
        ui._request_exit()
        if kind == 'coding':
            await manager.aclose()
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            session.dispose()
        else:
            connection.cancel()
            await asyncio.gather(connection, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_native_required_tools_not_selected_refuses_before_model_or_output_review(tmp_path, kind):
    server, connection, writer = None, None, None
    if kind == 'coding':
        configs, log = task_config(tmp_path, 'sampling')
        configs['owned']['args'].extend(['60000', 'required'])
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'mcp_servers': configs}))
        sessions, adapter = SessionManager(tmp_path / 'sessions'), Adapter()
        session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='explicit-model')),
            session_data=sessions.create_session(model='explicit-model', working_dir=tmp_path),
            session_manager=sessions, settings=SettingsManager(global_path=settings), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('deny'))
        manager, ui = session.mcp, InteractiveMode(session)
    else:
        server, config, runtime, session, adapter, log = await claw_setup(tmp_path, 'sampling')
        from dataclasses import replace
        original = runtime.mcp.configs['owned']
        runtime.mcp.configs['owned'] = replace(original, command=(*original.command, '60000', 'required'))
        manager, ui = runtime.mcp, ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        connection = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    def frames():
        return [json.loads(raw) for raw in log.read_text().splitlines()]
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready and ui._mcp_sampling_supported)
        ui._editor.set_text('PARENT REQUIRED TOOL DRAFT')
        ui._open_mcp()
        await wait(lambda: not ui._mcp_panel._busy)
        panel = ui._mcp_panel
        panel.handle_input(KeyEvent('j'))
        await wait(lambda: not panel._busy)
        panel.handle_input(KeyEvent('c'))
        await wait(lambda: panel.sampling_review is not None)
        first = panel.sampling_review
        assert first.event['body']['params']['toolChoice'] == {'mode': 'required'}
        first.editor.set_text(json.dumps({'model': 'explicit-model', 'max_tokens': 10,
                                         'context': 'none', 'tools': []}))
        first.handle_input(KeyEvent('enter'))
        paint_sampling(first)
        first.handle_input(KeyEvent('enter'))
        await wait(lambda: any(row.get('id') == 'retrieve' for row in frames()))
        assert next(row for row in frames() if row.get('id') == 'retrieve')['error']['code'] == -32000
        await wait(lambda: panel.sampling_review is None)
        assert not adapter.calls and not session.agent.messages and not panel._busy
        assert ui._editor.get_text() == 'PARENT REQUIRED TOOL DRAFT'
        if kind == 'coding':
            assert session.permissions.mode == 'deny' and not session._auth_changing
        else:
            assert not server._orchestrator.is_group_running('main')
    finally:
        ui._request_exit()
        if kind == 'coding':
            await manager.aclose()
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            session.dispose()
        else:
            connection.cancel()
            await asyncio.gather(connection, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()
