"""Native Claw input is local; no parent/child terminal-hook negotiation remains."""
from __future__ import annotations

from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.tui.stdin_buffer import KeyBytes, Paste


def test_parent_and_child_terminal_hook_apis_are_absent():
    ui = ChatApp('/unused')
    for name in ('_terminal_input', '_terminal_supported', '_terminal_release_supported',
                 '_terminal_child_supported', '_terminal_available', '_send_terminal_input',
                 '_accept_child_terminal_metadata', '_terminal_input_result'):
        assert not hasattr(ui, name)
    assert not hasattr(ui._tui, 'set_frame_interceptor')
    assert 'terminal_input' not in ui._keymap.bindings


def test_native_draft_paste_release_and_repeat_do_not_start_a_run():
    ui = ChatApp('/unused')
    submitted = []
    ui._editor.on_submit = submitted.append
    ui._tui._handle_frame(KeyBytes(b'local'))
    ui._tui._handle_frame(KeyBytes(b'\x1b[13;1:3u\x1b[99;5:3u'))
    ui._tui._handle_frame(Paste('\n/exit\ryes'))
    assert not submitted and ui._active_run_id is None and ui._send_queue.empty()
    assert 'local' in ui._editor.get_text() and '/exit' in ui._editor.get_text()
    before = ui._editor.get_text()
    ui._tui._handle_frame(KeyBytes(b'\x1b[97;1:2u'))
    assert ui._editor.get_text() == before + 'a'


def test_old_remote_terminal_result_cannot_replace_or_submit_native_draft():
    ui = ChatApp('/unused')
    ui._editor.set_text('unchanged')
    for kind in ('terminal_input_result', 'child_terminal_registry'):
        ui._handle_response({'type': kind, 'group_id': ui.group_id, 'data': '\ryes',
                             'consume': False, 'error': False})
    assert ui._editor.get_text() == 'unchanged'
    assert ui._active_run_id is None and ui._send_queue.empty()
