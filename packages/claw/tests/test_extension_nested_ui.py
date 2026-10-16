"""Native Claw refuses removed nested/custom/form UI without touching the parent draft."""
from __future__ import annotations

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.tui.components.extension_dialog import ExtensionDialog
from xdog.tui.keys import KeyEvent


@pytest.mark.parametrize('kind,extra', [('custom', {'view_id': 'old'}), ('form', {'fields': []}),
                                      ('input', {'parent_id': 'foreign'})])
def test_removed_nested_dialog_packets_cannot_take_focus_or_borrow_a_parent(kind, extra):
    ui = ChatApp('/unused')
    ui._active_run_id = 'run'
    ui._gateway_session_id = 'session'
    ui._editor.set_text('parent draft')
    original = ui._tui._focused
    ui._handle_response({'type': 'extension_dialog', 'group_id': ui.group_id, 'run_id': 'run',
                         'session_id': 'session', 'extension': 'legacy',
                         'dialog': {'kind': kind, 'id': 'old', 'title': 'Old UI', **extra}})
    assert ui._extension_dialog_panel is None and not ui._extension_dialog_queue
    assert ui._tui._focused is original and ui._editor.get_text() == 'parent draft'
    assert not ui._tui.has_overlay() and not ui._tui.fullscreen and ui._send_queue.empty()


def test_basic_confirm_still_has_visible_false_default_review_and_restores_parent_focus():
    ui = ChatApp('/unused')
    ui._active_run_id = 'run'
    ui._gateway_session_id = 'session'
    ui._editor.set_text('parent draft')
    ui._handle_response({'type': 'extension_dialog', 'group_id': ui.group_id, 'run_id': 'run',
                         'session_id': 'session', 'extension': 'simple',
                         'dialog': {'kind': 'confirm', 'id': 'question', 'title': 'Proceed?'}})
    panel = ui._extension_dialog_panel
    assert isinstance(panel, ExtensionDialog) and ui._tui._focused is panel
    panel.handle_input(KeyEvent('enter'))
    assert ui._send_queue.empty()  # Not painted: never confirm unseen data.
    panel.render(80)
    panel.handle_paste('yes\n')
    assert ui._send_queue.empty()
    panel.handle_input(KeyEvent('escape'))
    packet = ui._send_queue.get_nowait()
    assert packet['type'] == 'extension_dialog_answer' and packet['value'] is None
    assert ui._editor.get_text() == 'parent draft'
    assert ui._tui._focused is ui._editor and ui._extension_dialog_panel is None
