"""UI032 native hosts expose read-only extension queries, not UI/session mutation authority."""
from __future__ import annotations

import asyncio
import base64

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.core.permissions import PermissionRequest
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.attachments import attachment

from packages.coding.tests.extension_session_helpers import PNG, close, session


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('kind,operation', [('editor', 'read'), ('editor', 'replace'), ('editor', 'propose'),
    *[('session', item) for item in ('rename', 'select_tools', 'context_message', 'message', 'queue_input',
                                    'compact', 'browse', 'navigate')]])
def test_removed_control_dialogs_cannot_adopt_owner_mutate_draft_or_displace_approval(
        coding_session, tmp_path, monkeypatch, host, kind, operation):
    monkeypatch.setenv('CODING_DIR', str(tmp_path))
    monkeypatch.setenv('CLAW_DIR', str(tmp_path))
    ui = InteractiveMode(coding_session) if host == 'coding' else ChatApp('/unused')
    ui._editor.set_text('PRIVATE UNSENT DRAFT')
    image = attachment(base64.b64decode(PNG), 'private.png')
    ui._composer.items[image.id] = image
    if host == 'coding':
        ui._show_permission_request(PermissionRequest(id='protected', tool_name='bash', arguments={}, summary='Run'))
    before = ui._tui._focused, ui._command if host == 'coding' else ui._extension_pending
    deliver = ui._handle_ui_event if host == 'coding' else ui._handle_response
    packet = {'type': 'extension_dialog', 'extension_session': coding_session.session_id,
              'group_id': getattr(ui, 'group_id', ''), 'run_id': 'retired', 'request_id': 'retired',
              'session_id': 'retired', 'extension': 'legacy',
              'dialog': {'kind': kind, 'operation': operation, 'id': 'old', 'title': 'Removed UI'}}
    try:
        deliver(packet)
        deliver({**packet, 'type': 'child_extension_dialog', 'child_id': 'a' * 12, 'child_owner': 'b' * 32})
        assert ui._editor.get_text() == 'PRIVATE UNSENT DRAFT' and ui._composer.items[image.id] is image
        assert not ui._extension_dialog_queue and ui._extension_dialog_panel is None
        assert (ui._tui._focused, ui._command if host == 'coding' else ui._extension_pending) == before
        if host == 'coding':
            assert not coding_session.agent.is_streaming
        else:
            assert ui._gateway_session_id == '' and ui._send_queue.empty() and ui._active_run_id is None
    finally:
        ui._request_exit()


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('kind', ['extension_editor_write_commit', 'child_extension_editor_write_commit',
    'extension_input_queued', 'child_extension_input_queued', 'extension_dialog_accepted',
    'extension_context_continuation', 'extension_context_delivery_result', 'extension_context_admitted',
    'extension_compaction_after_run', 'extension_name_after_run', 'extension_active_tools_after_run'])
def test_retired_receipts_do_not_run_a_followup_or_change_current_host_work(
        coding_session, tmp_path, monkeypatch, host, kind):
    monkeypatch.setenv('CODING_DIR', str(tmp_path))
    monkeypatch.setenv('CLAW_DIR', str(tmp_path))
    ui = InteractiveMode(coding_session) if host == 'coding' else ChatApp('/unused')
    ui._editor.set_text('UNCHANGED')
    before = ui._tui._focused, ui._output_epoch
    deliver = ui._handle_ui_event if host == 'coding' else ui._handle_response
    try:
        deliver({'type': kind, 'extension_session': coding_session.session_id, 'group_id': getattr(ui, 'group_id', ''),
                 'id': 'legacy', 'operation': 'navigate', 'success': True, 'continuation': True, 'token': 'a' * 32})
        assert ui._editor.get_text() == 'UNCHANGED' and (ui._tui._focused, ui._output_epoch) == before
        assert not ui._extension_dialog_queue
        if host == 'coding':
            assert not coding_session.agent.is_streaming and ui._session_events.runtime is None
        else:
            assert ui._active_run_id is None and ui._send_queue.empty()
    finally:
        ui._request_exit()


async def test_actual_native_command_queries_keep_editor_images_name_permissions_and_zero_model_calls(tmp_path):
    owner, model_calls = session(tmp_path, extra=('readonlyhost',))
    ui = InteractiveMode(owner)
    image = attachment(base64.b64decode(PNG), 'retained.png')
    ui._editor.set_text('DO NOT READ OR REPLACE')
    ui._editor._cursor = 3
    ui._composer.items[image.id] = image
    try:
        ui._handle_slash_command('ext-readonlyhost-probe', '')
        async with asyncio.timeout(8):
            while ui._command is not None:
                ui._poll()
                await asyncio.sleep(.003)
        rows = owner.extensions.entries.for_extension('readonlyhost')
        probe = next(row['data'] for row in reversed(rows) if row['kind'] == 'readonly_probe')
        assert probe['session']['session_id'] == owner.session_id
        assert probe['name'] == owner._extension_session_name()['name']
        assert probe['model'] is not None and probe['usage'] is not None
        assert probe['tools'] is not None and probe['active'] is not None
        commands = probe['commands']
        assert commands is not None, 'The retained read-only terminal-menu query must reach its native owner'
        from xdog.tui.command_help import normalize_command_menu
        native_menu = normalize_command_menu(ui._editor._command_provider())
        assert commands['session_id'] == owner.session_id and commands['source'] == 'terminal-menu'
        assert commands['status'] == 'ok' and commands['total'] == len(native_menu)
        assert commands['commands'] == [
            {'name': name, 'description': detail}
            for name, detail in sorted(native_menu.items(), key=lambda row: row[0].casefold())[:32]]
        assert probe['pending'] is False
        assert probe['sessions'] is not None and probe['tree'] is not None
        assert ui._editor.get_text() == 'DO NOT READ OR REPLACE' and ui._editor._cursor == 3
        assert ui._composer.items[image.id] is image and owner.permissions.mode == 'deny'
        assert not model_calls and not owner.agent.messages and ui._navigation_panel is None
    finally:
        ui._request_exit()
        await close(owner)


@pytest.mark.parametrize('command', ['model', 'compact', 'name', 'sessions', 'tools'])
def test_native_user_commands_still_enter_their_original_handlers(coding_session, monkeypatch, command):
    ui = InteractiveMode(coding_session)
    dispatched = []
    monkeypatch.setattr(ui, '_run_command', lambda value, args: dispatched.append((value.name, args)))
    monkeypatch.setattr(ui, '_open_navigation', lambda scope='sessions': dispatched.append(('sessions', scope)))
    monkeypatch.setattr(ui, '_open_tools', lambda query='': dispatched.append(('tools', query)))
    try:
        ui._handle_slash_command(command, '' if command in {'sessions', 'tools'} else 'explicit user value')
        if ui._command_thread is not None:
            ui._command_thread.join(3)
        assert dispatched and dispatched[0][0] == command
    finally:
        ui._command = None
        ui._request_exit()


@pytest.mark.parametrize('command,packet_type', [('model', 'extension_model_control'),
    ('compact', 'session_compact'), ('name', 'session_control'), ('sessions', None), ('tools', None)])
def test_claw_native_user_model_name_compact_and_catalog_commands_remain_independent(command, packet_type):
    ui = ChatApp('/unused')
    ui._connection_ready = True
    ui._gateway_session_id = 'native-owner'
    ui._extension_models_supported = True  # Native USER model picker, not an SDK mutation grant.
    ui._native_models_supported = True
    ui._compaction_supported = True
    ui._session_rename_supported = True
    opened = []
    ui._open_navigation = lambda scope='sessions': opened.append(('sessions', scope))
    ui._open_tools = lambda query='': opened.append(('tools', query))
    try:
        ui._handle_submit('/' + command + ('' if command in {'sessions', 'tools'} else ' explicit user value'))
        if packet_type is None:
            assert opened and opened[0][0] == command and ui._send_queue.empty()
        else:
            packet = ui._send_queue.get_nowait()
            assert packet['type'] == packet_type
            assert packet.get('session_id', packet.get('expected_session')) == 'native-owner'
            assert not {'extension_message_token', 'extension_context_token',
                        'extension_compaction_generation'} & packet.keys()
        assert ui._active_run_id is None
    finally:
        ui._request_exit()


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('kind', ['extension_dialog_accepted', 'extension_editor_write_commit',
                                  'extension_context_continuation', 'extension_input_queued'])
def test_retired_receipt_cannot_settle_a_newer_command_or_question_with_reused_identifier(coding_session, host, kind):
    from xdog.coding.modes.interactive.interactive_mode import _CommandState
    ui = InteractiveMode(coding_session) if host == 'coding' else ChatApp('/unused')
    if host == 'coding':
        ui._command = _CommandState(777, 'ext-current-probe', False)
    else:
        ui._extension_pending = 'new-current', 'native-owner'
    current = {'id': 'same-id', 'kind': 'input', 'title': 'Current question'}
    ui._extension_dialog_queue.append(current)
    command = ui._command if host == 'coding' else ui._extension_pending
    try:
        packet = {'type': kind, 'id': 'same-id', 'request_id': 'new-current', 'group_id': getattr(ui, 'group_id', ''),
                  'extension_session': coding_session.session_id, 'operation': 'navigate', 'success': True}
        (ui._handle_ui_event if host == 'coding' else ui._handle_response)(packet)
        assert ui._extension_dialog_queue == [current]
        assert (ui._command if host == 'coding' else ui._extension_pending) == command
        assert not ui._routed_inputs
    finally:
        if host == 'coding':
            ui._command = None
        else:
            ui._extension_pending = None
        ui._extension_dialog_queue.clear()
        ui._request_exit()
