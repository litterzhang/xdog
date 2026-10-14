from __future__ import annotations

from xdog.claw.channels.tui.tui_app import ChatApp


def test_name_queue_cannot_reuse_withdrawn_or_foreign_command_owner():
    ui = ChatApp('/tmp/unused-extension-name.sock')
    try:
        ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_rename': 1,
            'extension_session': 1, 'extension_session_name': 1, 'session_id': 'session'})
        context = ui._mcp_context_key()
        target = {'action': 'rename', 'name': 'Never apply', 'expected_name': ''}
        ui._extension_navigation = 'old', context, target
        assert not ui._finish_extension_navigation('new', True)
        ui._extension_navigation = 'owner', context, target
        ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_rename': 1,
                             'extension_session': 1, 'session_id': 'session'})
        assert not ui._finish_extension_navigation('owner', True)
        assert ui._send_queue.empty()
    finally:
        ui._request_exit()
