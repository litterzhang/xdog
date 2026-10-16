"""Retired accepted/ACK receipts cannot dispatch native session commands."""
import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('kind', ['extension_dialog_accepted', 'extension_dialog_ack'])
def test_retired_navigation_receipts_do_not_dispatch_user_commands(coding_session, host, kind):
    ui = InteractiveMode(coding_session) if host == 'coding' else ChatApp('/unused')
    sent = []
    ui._editor.set_text('unchanged draft')
    ui._handle_slash_command = lambda *args: sent.append(args)
    ui._navigate_session = lambda *args: sent.append(args)
    try:
        for name in ('_queue_extension_navigation', '_finish_extension_navigation', '_extension_navigation',
                     '_extension_navigation_review'):
            assert not hasattr(ui, name)
        packet = {'type': kind, 'id': 'stale', 'operation': 'navigate', 'accepted': True,
                  'extension_session': coding_session.session_id, 'group_id': getattr(ui, 'group_id', '')}
        (ui._handle_ui_event if host == 'coding' else ui._handle_response)(packet)
        assert not sent and ui._editor.get_text() == 'unchanged draft'
    finally:
        ui._request_exit()
