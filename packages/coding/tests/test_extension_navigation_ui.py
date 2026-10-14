from __future__ import annotations

from pathlib import Path

import pytest
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


def test_stale_or_failed_reviewed_navigation_does_not_dispatch_session_commands(tmp_path):
    from unittest.mock import MagicMock
    session = MagicMock()
    session.session_id = 'current'
    session.working_dir = tmp_path
    ui = InteractiveMode(session)
    sent = []
    ui._handle_slash_command = lambda *args: sent.append(args)
    try:
        target = {'action': 'new', 'session_id': '', 'branch_id': '', 'index': -1}
        ui._extension_navigation = ('old-command', ui._mcp_context_key(), target)
        assert not ui._finish_extension_navigation('new-command', True)
        ui._extension_navigation = ('command', ui._mcp_context_key(), target)
        assert not ui._finish_extension_navigation('command', False)
        ui._extension_navigation = ('command', ui._mcp_context_key(), target)
        ui._output_epoch += 1
        assert not ui._finish_extension_navigation('command', True)
        assert not sent
    finally:
        ui._request_exit()


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('mutation', ['none', 'id', 'extension', 'operation', 'owner', 'epoch', 'closed', 'ack'])
def test_native_navigation_requires_scoped_actual_receipt_not_transport_ack(tmp_path, kind, mutation):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from xdog.claw.channels.tui.tui_app import ChatApp

    if kind == 'coding':
        session = MagicMock()
        session.session_id = 'session'
        session.working_dir = tmp_path
        ui = InteractiveMode(session)
        ui._command = SimpleNamespace(id='owner', name='ext-navigator-navigate', cancel_requested=False, mutating=False)
        deliver = ui._handle_ui_event
    else:
        ui = ChatApp('/tmp/unused-receipt.sock')
        ui._gateway_session_id = 'session'
        ui._connection_ready = True
        ui._extension_navigation_supported = True
        ui._extension_pending = 'owner', 'session'
        ui._extension_editor_owner = 'owner', 'navigator'
        deliver = ui._handle_response
    try:
        ui._editor.set_text('unchanged draft')
        item = {'id': 'dialog', 'extension': 'navigator', 'kind': 'session', 'operation': 'navigate',
                'target': {'action': 'new', 'session_id': '', 'branch_id': '', 'index': -1}}
        assert ui._queue_extension_navigation(item, True)
        assert ui._extension_navigation is None and ui._extension_navigation_review is not None
        event = {'type': 'extension_dialog_accepted', 'id': 'dialog', 'extension': 'navigator',
                 'operation': 'navigate', 'extension_session': 'session', 'request_id': 'owner',
                 'group_id': ui.group_id if kind == 'claw' else ''}
        if mutation in {'id', 'extension', 'operation'}:
            event[mutation] = 'foreign'
        elif mutation == 'owner':
            if kind == 'coding':
                ui._command.id = 'foreign'
            else:
                event['request_id'] = 'foreign'
        elif mutation == 'epoch':
            ui._output_epoch += 1
        elif mutation == 'closed':
            event['type'] = 'extension_dialog_closed'
        elif mutation == 'ack':
            event.update(type='extension_dialog_ack', accepted=True)
        deliver(event)
        assert (ui._extension_navigation is not None) is (mutation == 'none')
        assert ui._editor.get_text() == 'unchanged draft'
        assert not ui._finish_extension_navigation('owner', False)
        assert ui._extension_navigation is None and ui._extension_navigation_review is None
    finally:
        if kind == 'coding':
            ui._command = None
        else:
            ui._extension_pending = None
        ui._request_exit()
