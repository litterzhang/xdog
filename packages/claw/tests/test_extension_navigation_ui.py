"""Retired plugin navigation cannot trigger native session controls or acquire focus."""
import pytest
from xdog.claw.channels.tui.tui_app import ChatApp


@pytest.mark.parametrize('operation', ['browse', 'navigate', 'rename', 'compact', 'select_tools', 'message'])
def test_old_extension_navigation_question_is_inert_before_session_adoption(operation):
    ui = ChatApp('/unused')
    ui._extension_pending = 'current', ''
    ui._editor.set_text('PRIVATE DRAFT')
    focus = ui._tui._focused
    ui._handle_response({'type': 'extension_dialog', 'group_id': ui.group_id, 'request_id': 'current',
                         'session_id': 'retired', 'extension': 'legacy',
                         'dialog': {'id': 'old', 'kind': 'session', 'operation': operation, 'title': 'Removed'}})
    assert ui._extension_pending == ('current', '') and ui._gateway_session_id == ''
    assert ui._tui._focused is focus and ui._editor.get_text() == 'PRIVATE DRAFT'
    assert not ui._extension_dialog_queue and ui._send_queue.empty()
