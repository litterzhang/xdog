from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.ai.types import AssistantMessage, Model
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.components.tool_inventory import ToolInventoryPanel
from xdog.tui.keys import KeyEvent


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('capability', [False, True])
async def test_actual_tools_browser_full_schema_query_refresh_no_model_and_native_parent(tmp_path, kind, capability):
    calls = []
    async def execute(*args):
        pytest.fail('The tools browser cannot execute a tool')
        return AgentToolResult()
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    tools = tuple(AgentTool(name='probe_' + str(index), description='Public definition',
        parameters={'type': 'object', 'properties': {'value': {'enum': [False, 0, None]},
            'tail': {'description': 'untruncated-schema-' + str(index)}}}, execute=execute) for index in range(75))
    server = task = writer = None
    if kind == 'coding':
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream,
            config=AgentConfig(model='test/model', supports_tool_calls=capability),
            tools=tools), session_data=store.create_session(working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
            tool_repo=None, bash=None, permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
    else:
        class Adapter:
            def resolve_model(self, name):
                return Model(id='test/model', supports_tool_calls=capability)
            def __call__(self, *args):
                return stream(*args)
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'tools.sock'), pid_file=str(tmp_path / 'tools.pid'),
            groups=(GroupDef(id='main', is_main=True),))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', Adapter())):
            await server.start()
        session = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
        session.agent.set_tools((*session.agent.state.tools, *tools))
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                if ui._transcript_panel is not None:
                    ui._transcript_panel.render(80)
                await asyncio.sleep(0.003)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
            assert ui._tool_inventory_supported
        session.agent.set_active_tools(['probe_74'], expected_revision=session.agent.tool_selection_revision)
        ui._editor.set_text('unchanged tools draft')
        ui._editor._cursor = 5
        if kind == 'coding':
            ui._handle_slash_command('tools', 'untruncated-schema-74')
        else:
            ui._handle_submit('/tools untruncated-schema-74')
        await wait(lambda: isinstance(ui._transcript_panel, ToolInventoryPanel) and ui._transcript_panel.settled)
        panel = ui._transcript_panel
        assert len([record for record in panel.records if record.title.startswith('probe_')]) == 75
        assert ('disabled' if not capability else 'enabled/unknown support') in panel.records[0].body
        assert 'Selection: explicit' in panel.records[0].body and 'selected 1 /' in panel.records[0].body
        assert 'Selection: selected' in next(row.body for row in panel.records if row.title == 'probe_74')
        assert 'Selection: excluded' in next(row.body for row in panel.records if row.title == 'probe_0')

        assert '"enum":[false,0,null]' in next(record.body for record in panel.records if record.title == 'probe_74')
        assert panel.browser.snapshot().matches == 1 and ui._tui._focused is panel
        assert any('Tools (' in line for line in panel.render(80))
        captured = []
        original_output = ui._outputs.start
        ui._outputs.start = lambda *args, **kwargs: captured.append(args)
        ui._output_from_browser('export', 'entry')
        assert captured and captured[-1][1] == panel.records
        assert all(record.id.startswith('tools:') for record in captured[-1][1])
        assert ui._source_request_id is None if kind == 'claw' else True
        ui._outputs.start = original_output
        ui._tui._running = True  # Virtual mode; production alternate-buffer behavior has terminal tests.
        panel.handle_input(KeyEvent('f'))
        assert panel.fullscreen and ui._layout.fullscreen_view is panel
        panel.handle_input(KeyEvent('f'))
        assert not panel.fullscreen and ui._layout.fullscreen_view is None
        ui._tui._screen_change = None
        ui._tui._running = False
        # Configured inventory changes are explicitly refreshed, not local model turns.
        session.agent.set_tools(tuple(tool for tool in session.agent.state.tools if tool.name != 'probe_74'))
        panel.handle_input(KeyEvent('r'))
        await wait(lambda: panel.settled and not any(record.title == 'probe_74' for record in panel.records))
        assert not calls and not session.agent.state.messages
        if kind == 'coding':
            old = ui._tools_request
            ui._handle_ui_event({'type': 'tools_ready', 'id': old, 'context': 'foreign', 'records': ()})
        else:
            ui._handle_response({'type': 'tool_inventory_result', 'request_id': ui._tools_request,
                'group_id': 'foreign', 'session_id': ui._gateway_session_id, 'success': True, 'snapshot': {}})
        assert any(record.title == 'probe_73' for record in panel.records)
        panel.handle_input(KeyEvent('escape'))
        assert ui._transcript_panel is None and ui._tui._focused is ui._editor
        assert ui._editor.get_text() == 'unchanged tools draft' and ui._editor._cursor == 5
        if kind == 'coding':
            assert session.permissions.mode == 'deny'
    finally:
        ui._request_exit()
        await session.children.runtime.aclose()
        await session.extensions.aclose()
        if kind == 'coding':
            session.dispose()
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()


async def test_gateway_inventory_capability_owner_cancel_and_reader_responsiveness(tmp_path):
    import threading
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'caps.sock'), pid_file=str(tmp_path / 'caps.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=('test/model', lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    session = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    async def send(value):
        writer.write((json.dumps(value) + '\n').encode())
        await writer.drain()
    async def receive(kind):
        async with asyncio.timeout(3):
            while True:
                value = json.loads(await reader.readline())
                if value['type'] == kind:
                    return value
    gate, started = threading.Event(), threading.Event()
    original = session._extension_tools
    def blocked():
        started.set()
        gate.wait(3)
        return original()
    base = {'type': 'tool_inventory_read', 'group_id': 'main', 'session_id': session.meta.session_id}
    try:
        await send({'type': 'ping', 'group_id': 'main'})
        await receive('pong')
        await send({**base, 'request_id': 'old'})
        assert not (await receive('tool_inventory_result'))['success']
        await send({'type': 'ping', 'tool_inventory': 1})
        assert (await receive('pong'))['tool_inventory_selection'] == 1
        await send({**base, 'request_id': 'legacy'})
        legacy = await receive('tool_inventory_result')
        assert legacy['success'] and set(legacy['snapshot']) == {
            'session_id', 'owner_id', 'model', 'api_enabled', 'tools'}
        session.agent.set_active_tools([], expected_revision=session.agent.tool_selection_revision)
        await send({**base, 'request_id': 'selected', 'include_selection': True})
        selected = await receive('tool_inventory_result')
        assert selected['success'] and selected['snapshot']['active_tools'] == []
        assert selected['snapshot']['selection_mode'] == 'explicit' and selected['snapshot']['tools']
        await send({**base, 'request_id': 'invalid', 'include_selection': 1})
        assert not (await receive('tool_inventory_result'))['success']
        await send({**base, 'request_id': 'foreign', 'session_id': 'foreign'})
        assert not (await receive('tool_inventory_result'))['success']
        session._extension_tools = blocked
        await send({**base, 'request_id': 'active'})
        async with asyncio.timeout(3):
            while not started.is_set():
                await asyncio.sleep(0.003)
        await send({'type': 'ping'})
        assert (await receive('pong'))['type'] == 'pong'  # Read cannot block the socket reader.
        await send({'type': 'tool_inventory_cancel', 'request_id': 'active'})
        await send({'type': 'ping', 'tool_inventory': 0})
        await receive('pong')
        gate.set()
        session._extension_tools = original
        # Repeated same-connection cancellation cannot exhaust the eight-read quota.
        await send({'type': 'ping', 'tool_inventory': 1})
        await receive('pong')
        for index in range(12):
            rid = 'read-' + str(index)
            await send({**base, 'request_id': rid})
            result = await receive('tool_inventory_result')
            assert result['request_id'] == rid and result['success']
        assert not session.agent.state.messages
    finally:
        gate.set()
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_claw_tools_view_capability_withdrawal_never_falls_back_to_chat_or_footer():
    ui = ChatApp('/unused')
    ui._connection_ready = True
    ui._gateway_session_id = 'session'
    ui._tool_inventory_supported = True
    ui._open_tools()
    panel = ui._transcript_panel
    assert isinstance(panel, ToolInventoryPanel) and ui._source_request_id is None
    rid = ui._tools_request
    ui._tool_inventory_supported = False
    ui._handle_response({'type': 'tool_inventory_result', 'request_id': rid, 'group_id': ui.group_id,
        'session_id': 'session', 'success': True, 'snapshot': {}})
    assert not panel.records and 'unavailable' in panel._source_status.lower()
    ui._close_transcript()
    ui._handle_response({'type': 'tool_inventory_result', 'request_id': rid, 'group_id': ui.group_id,
        'session_id': 'session', 'success': True, 'snapshot': {}})
    assert ui._transcript_panel is None
    ui._request_exit()


async def test_headless_tools_command_is_complete_read_only_and_query_filtered(tmp_path):
    from xdog.coding.core.slash_commands import execute_command
    async def execute(*args):
        pytest.fail('Inspecting tools cannot execute them')
        return AgentToolResult()
    store = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(lambda *args: pytest.fail('No model call'), config=AgentConfig(model='model'),
        tools=tuple(AgentTool(name='native_' + str(index), parameters={'type': 'object'}, execute=execute)
                    for index in range(80))), session_data=store.create_session(working_dir=tmp_path),
        session_manager=store, settings=SettingsManager(global_path=tmp_path / 'settings.json'),
        working_dir=tmp_path, tool_repo=None, bash=None, permissions=PermissionManager('deny'))
    try:
        result = json.loads((await execute_command('tools', '', session)).output)
        assert len([tool for tool in result['tools'] if tool['name'].startswith('native_')]) == 80
        session.agent.set_active_tools(['native_0'], expected_revision=session.agent.tool_selection_revision)
        result = json.loads((await execute_command('tools', 'native_79', session)).output)
        assert [tool['name'] for tool in result['tools']] == ['native_79']
        assert result['active_tools'] == [] and result['selection_mode'] == 'explicit'
        assert session.permissions.mode == 'deny' and not session.agent.state.messages
    finally:
        await session.extensions.aclose()
        await session.children.runtime.aclose()
        session.dispose()


async def test_claw_inventory_reconnect_canonical_source_and_output_admission_are_separate():
    from xdog.tui.components.tool_inventory import inventory_records
    from xdog.tui.transcript_browser import TranscriptRecord
    from xdog.tui.transcript_transfer import TransferOptions
    ui = ChatApp('/unused')
    ui._connection_ready = True
    ui._gateway_session_id = 'owner'
    ui._tool_inventory_supported = True
    ui._open_tools()
    panel = ui._transcript_panel
    records = inventory_records({'model': 'model', 'api_enabled': True, 'tools': [
        {'name': 'probe', 'description': 'definition', 'parameters': {'type': 'object'}}]})
    panel.publish_inventory(records)
    panel.render(80)
    async with asyncio.timeout(3):
        while not panel.settled:
            panel.poll()
            await asyncio.sleep(0.003)
    ui._source_request_id = 'canonical'
    ui._source_session_id = 'owner'
    ui._handle_source_event({'type': 'source_ready', 'request_id': 'canonical', 'group_id': ui.group_id,
        'session_id': 'owner', 'records': (TranscriptRecord('private', 'user', 'history', 'PRIVATE-CONVERSATION'),)})
    assert panel.records == records and ui._source_records is None
    ui._source_request_id = None
    ui._request_full_source()
    assert ui._source_request_id is None
    pending = ui._tools_request
    ui._editor.set_text('retained reconnect draft')
    ui._handle_response({'type': '_connection_opened', 'generation': 1})
    assert not panel.records and not panel.inventory_ready and not ui._tools_request
    ui._handle_response({'type': 'tool_inventory_result', 'request_id': pending, 'group_id': ui.group_id,
        'session_id': 'owner', 'success': True, 'snapshot': {'model': 'model'}})
    assert not panel.records
    started = []
    ui._outputs.start = lambda *args, **kwargs: started.append(args)
    ui._begin_output(TransferOptions('export', 'conversation', 'json'))
    assert not started and ui._editor.get_text() == 'retained reconnect draft'
    ui._request_exit()


async def test_inventory_empty_valid_snapshot_differs_from_failed_refresh_and_blocks_all_outputs(tmp_path):
    from xdog.tui.components.tool_inventory import inventory_records
    from xdog.tui.transcript_transfer import TransferOptions
    store = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(lambda *args: pytest.fail('No model'), config=AgentConfig(model='model')),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
        tool_repo=None, bash=None, permissions=PermissionManager('deny'))
    ui = InteractiveMode(session)
    panel = ToolInventoryPanel(lambda: (), lambda: None, lambda: None)
    ui._transcript_panel = panel
    ui._tools_context = ui._mcp_context_key()
    try:
        panel.publish_inventory(inventory_records({'model': 'model', 'api_enabled': False, 'tools': []}))
        panel.render(80)
        async with asyncio.timeout(3):
            while not panel.settled:
                panel.poll()
                await asyncio.sleep(0.003)
        assert panel.settled and 'Configured definitions: 0' in panel.records[0].body
        started = []
        ui._outputs.start = lambda *args, **kwargs: started.append(args)
        panel.begin_inventory_read()
        for scope in ('entry', 'conversation', 'all', 'response'):
            ui._begin_output(TransferOptions('export', scope, 'json'))
        assert not started
        panel.invalidate_inventory('Owner unavailable')
        for scope in ('entry', 'conversation', 'all', 'response'):
            ui._begin_output(TransferOptions('export', scope, 'json'))
        assert not started and not panel.settled and not panel.records
    finally:
        ui._request_exit()
        await session.extensions.aclose()
        await session.children.runtime.aclose()
        session.dispose()


async def test_actual_claw_tools_disconnect_reconnect_requires_explicit_current_owner_refresh(tmp_path):
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'reconnect.sock'), pid_file=str(tmp_path / 'reconnect.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    connections = []
    async def connect():
        reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
        task = asyncio.create_task(ui._run_connection(reader, writer))
        connections.append((writer, task))
        return writer, task
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                if ui._transcript_panel is not None:
                    ui._transcript_panel.render(80)
                await asyncio.sleep(0.003)
    try:
        writer, first = await connect()
        await wait(lambda: ui._connection_ready)
        ui._editor.set_text('reconnect parent draft')
        ui._open_tools()
        await wait(lambda: ui._transcript_panel.settled)
        panel = ui._transcript_panel
        session = server._orchestrator._runtimes['main']._active_session
        assert panel.inventory_ready and panel.records
        writer.close()
        await writer.wait_closed()
        await wait(lambda: not ui._connection_ready)
        assert not panel.inventory_ready and not panel.records and not ui._tools_request
        await asyncio.gather(first, return_exceptions=True)
        session.agent.set_tools(())
        await connect()
        await wait(lambda: ui._connection_ready)
        assert ui._transcript_panel is panel and not panel.inventory_ready and not ui._tools_request
        panel.handle_input(KeyEvent('r'))
        await wait(lambda: panel.settled)
        assert panel.inventory_ready and 'Configured definitions: 0' in panel.records[0].body
        assert not calls and not session.agent.state.messages
        panel.handle_input(KeyEvent('escape'))
        assert ui._tui._focused is ui._editor and ui._editor.get_text() == 'reconnect parent draft'
    finally:
        ui._request_exit()
        for writer, task in connections:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
        await server.stop()
