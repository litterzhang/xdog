"""Removed extension name capability cannot grant session mutation; native /name stays separate."""
from xdog.claw.channels.tui.tui_app import ChatApp


def test_legacy_extension_name_capability_cannot_enable_a_name_control():
    ui = ChatApp('/unused')
    ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_rename': 1,
                         'session_name_read': 1, 'extension_session_name': 1, 'session_id': 'session'})
    assert not hasattr(ui, '_extension_name_supported') and not hasattr(ui, '_finish_extension_navigation')
    assert ui._session_rename_supported and ui._name_read_supported
    assert ui._send_queue.empty()
