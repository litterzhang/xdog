"""Saved source viewing uses actual client/session owners without navigation or model work."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.messages import messages_to_dicts
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.persistence.transcript_convert import messages_to_transcript
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent
from xdog.tui.transcript_transfer import TransferOptions


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('scope', ['branch', 'other-session'])
async def test_saved_full_source_view_search_output_no_activation_model_or_draft_change(tmp_path, kind, scope):
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = task = writer = None
    if kind == 'coding':
        manager = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
            session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
            settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'saved.sock'), pid_file=str(tmp_path / 'saved.pid'),
            groups=(GroupDef(id='main', is_main=True),))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', stream)):
            await server.start()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=8 * 1024 * 1024)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
            assert ui._saved_source_supported
            ui._skills_command('list')
            await wait(lambda: not ui._skills_request)
            session = server._orchestrator._runtimes['main']._active_session
        live = [UserMessage(content='CURRENT-LIVE-ONLY'),
                AssistantMessage(content=(TextContent(text='current response'),))]
        archived = [UserMessage(content='ARCHIVED-USER ' + 'x' * 80000 + ' END-OF-ARCHIVE-NEEDLE'),
            AssistantMessage(content=(ToolCall(id='old', name='filesystem', arguments={'path': 'old.py',
                'api_key': 'PRIVATE-KEY-DO-NOT-DISPLAY'}),)),
            ToolResultMessage(tool_call_id='old', tool_name='filesystem',
                              content=(TextContent(text='ARCHIVED-TOOL-END'),)),
            AssistantMessage(content=(TextContent(text='ARCHIVED-ANSWER'),))]
        owner_id = session.session_id if kind == 'coding' else session.meta.session_id
        session.agent.replace_messages(live)
        if kind == 'coding':
            session._persist()
            if scope == 'branch':
                session.session_data.branches.append({'branch_id': 'old-branch',
                    'messages': messages_to_dicts(archived),
                    'branch_point': 3, 'label': 'Before compaction archived'})
                session._persist()
                target_id, branch_id = owner_id, 'old-branch'
            else:
                target = manager.create_session(working_dir=tmp_path, model='offline/model')
                target.messages = archived
                manager.save_session(target)
                target_id, branch_id = target.session_id, ''
        else:
            store = session._store
            store.replace_transcript(owner_id, messages_to_transcript(live))
            session._restored = True
            session._persisted_count = len(live)
            if scope == 'branch':
                store.save_branch(owner_id, 'old-branch', messages_to_transcript(archived),
                                  metadata={'label': 'Before compaction archived'})
                target_id, branch_id = owner_id, 'old-branch'
            else:
                target = store.create_session('main')
                store.replace_transcript(target.session_id, messages_to_transcript(archived))
                store.activate_session('main', owner_id)
                target_id, branch_id = target.session_id, ''
        before = session.agent.state.messages
        ui._editor.set_text('UNSUBMITTED-PARENT-DRAFT')
        # Use the actual native browser gesture, not session.restore_branch().
        ui._open_navigation('tree|' + owner_id + '|') if scope == 'branch' else ui._open_navigation()
        await wait(lambda: ui._navigation_panel is not None and any(
            entry.session_id == target_id and entry.branch_id == branch_id
            for entry in ui._navigation_panel._view.entries))
        panel = ui._navigation_panel
        panel._selected = next(index for index, entry in enumerate(panel._view.entries)
                               if entry.session_id == target_id and entry.branch_id == branch_id)
        ui._tui._dispatch_input(KeyEvent('v'))
        try:
            await wait(lambda: ui._transcript_panel is not None and ui._transcript_panel.settled
                       and 'END-OF-ARCHIVE-NEEDLE' in str(ui._transcript_panel.records))
        except TimeoutError:
            raise AssertionError((ui._saved_scope, ui._transcript_panel._source_status,
                                  str(ui._chat_log.render(100))[-1000:])) from None
        panel = ui._transcript_panel
        assert ui._saved_scope == (target_id, branch_id) and 'read-only' in panel.caption
        assert 'CURRENT-LIVE-ONLY' not in str(panel.records)
        assert 'PRIVATE-KEY-DO-NOT-DISPLAY' not in str(panel.records)
        assert 'ARCHIVED-TOOL-END' in str(panel.records)
        panel.search('END-OF-ARCHIVE-NEEDLE')
        await wait(lambda: panel.settled and bool(panel._view.matches))
        assert ui._editor.get_text() == 'UNSUBMITTED-PARENT-DRAFT'
        assert session.agent.state.messages == before and not calls
        if kind == 'coding':
            assert session.session_id == owner_id and session.permissions.mode == 'deny'
            assert not session.session_data.active_branch
        else:
            assert ui._gateway_session_id == owner_id
            assert server._orchestrator._runtimes['main']._active_session is session
            assert not session._store.active_branch(owner_id)
        # Capture existing reviewed output workflow; it must use archived records,
        # not silently export the active conversation when scope is 'conversation'.
        captured = []
        ui._outputs.start = lambda *args, **kwargs: captured.append(args)
        ui._begin_output(TransferOptions('export', 'conversation', 'md'))
        assert captured and 'END-OF-ARCHIVE-NEEDLE' in str(captured[0][1])
        assert 'CURRENT-LIVE-ONLY' not in str(captured[0][1])
        ui._close_transcript()
        assert ui._saved_scope is None and ui._editor.get_text() == 'UNSUBMITTED-PARENT-DRAFT'
    finally:
        ui._request_exit()
        if 'session' in locals():
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


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_unavailable_or_loading_saved_view_cannot_export_or_use_live_source(tmp_path, kind):
    if kind == 'claw':
        ui = ChatApp('/tmp/saved-source-offline.sock')
        ui._connection_ready = True
        ui._source_supported = True
        ui._gateway_session_id = 'owner'
        ui._saved_source_supported = False
    else:
        manager = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(lambda *args: EventStream.empty(AssistantMessage())),
            session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
            settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
            tool_registry=None, bash=None)
        ui = InteractiveMode(session)
    try:
        ui._chat_log.add_user('MUST-NOT-BECOME-ARCHIVE')
        ui._open_transcript(saved=('missing-saved-session', 'missing-branch'))
        captured = []
        ui._outputs.start = lambda *args, **kwargs: captured.append(args)
        ui._begin_output(TransferOptions('copy', 'conversation', 'text'))
        assert not captured and not ui._transcript_panel.settled
        assert 'MUST-NOT-BECOME-ARCHIVE' not in str(ui._transcript_panel.records)
        if kind == 'claw':
            assert ui._source_request_id is None
    finally:
        ui._request_exit()
        if kind == 'coding':
            session.dispose()


async def test_claw_saved_source_other_group_denied_and_capability_withdrawal_not_live_fallback(tmp_path):
    import json
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'foreign.sock'), pid_file=str(tmp_path / 'foreign.pid'),
        groups=(GroupDef(id='main', is_main=True), GroupDef(id='other')))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=('offline/model', lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    async def receive(kind):
        async with asyncio.timeout(5):
            while True:
                message = json.loads(await reader.readline())
                if message.get('type') == kind:
                    return message
    try:
        own = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
        foreign_runtime = server._orchestrator._runtimes['other']
        foreign = foreign_runtime.get_or_create_session(allow_reset=False)
        foreign._store.replace_transcript(foreign.meta.session_id, [{'role': 'user', 'content': 'FOREIGN-PRIVATE'}])
        writer.write((json.dumps({'type': 'ping', 'group_id': 'main', 'saved_source': 1}) + '\n').encode())
        await writer.drain()
        assert (await receive('pong'))['saved_source'] == 1
        request = {'type': 'saved_source_read', 'request_id': 'foreign', 'group_id': 'main',
                   'session_id': own.meta.session_id, 'target_session_id': foreign.meta.session_id, 'branch_id': ''}
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        error = await receive('source_error')
        assert 'another group' in error['error'] and 'FOREIGN-PRIVATE' not in str(error)
        writer.write((json.dumps({'type': 'ping', 'group_id': 'main', 'saved_source': 0}) + '\n').encode())
        await writer.drain()
        assert (await receive('pong'))['saved_source'] == 0
        writer.write((json.dumps({**request, 'request_id': 'withdrawn', 'target_session_id': own.meta.session_id})
                      + '\n').encode())
        await writer.drain()
        assert 'Updated saved-source' in (await receive('source_error'))['error']
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('scenario', ['stale-id', 'foreign-owner', 'after-close'])
def test_coding_saved_receipt_never_replaces_current_or_closed_view(tmp_path, scenario):
    from xdog.tui.transcript_browser import TranscriptRecord
    manager = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(lambda *args: EventStream.empty(AssistantMessage())),
        session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
        tool_registry=None, bash=None)
    ui = InteractiveMode(session)
    try:
        ui._open_transcript(saved=('missing', 'archive'))
        identifier, context = ui._saved_request, ui._saved_context
        if scenario == 'after-close':
            ui._close_transcript()
        elif scenario == 'foreign-owner':
            ui._output_epoch += 1
        ui._handle_ui_event({'type': 'saved_source_ready', 'id': 'other' if scenario == 'stale-id' else identifier,
            'context': context, 'records': (TranscriptRecord('secret', 'user', 'input', 'LATE-SAVED-DATA'),)})
        assert 'LATE-SAVED-DATA' not in str(ui._saved_records)
    finally:
        ui._request_exit()
        session.dispose()
