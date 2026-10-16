"""Native renderer/input invariants remain in both clients without plugin custom UI."""
from __future__ import annotations

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.core.permissions import PermissionRequest
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.components.text import Text
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import CURSOR_MARKER
from xdog.tui.utils import string_width


@pytest.fixture(params=['coding', 'claw'])
def ui(request, coding_session, tmp_path, monkeypatch):
    monkeypatch.setenv('CODING_DIR', str(tmp_path))
    monkeypatch.setenv('CLAW_DIR', str(tmp_path))
    app = InteractiveMode(coding_session) if request.param == 'coding' else ChatApp('/unused')
    try:
        yield app
    finally:
        app._request_exit()


def test_complex_extension_host_state_and_callback_transport_are_absent(ui):
    for name in ('_custom_presentation', '_request_extension_custom', '_extension_custom_supported',
                 '_extension_custom_nested_supported', '_extension_forms_supported'):
        assert not hasattr(ui, name)
    assert callable(ui._tui.show_overlay) and callable(ui._tui.request_fullscreen)


def test_native_unicode_editor_cursor_clipping_and_core_overlay_round_trip(ui):
    ui._editor.set_text('x👩\u200d💻界e\u0301')
    ui._editor.handle_input(KeyEvent('left'))
    text, cursor = ui._editor.get_text(), ui._editor._cursor
    lines = ui._editor.render(24)
    assert sum(line.count(CURSOR_MARKER) for line in lines) == 1
    assert all(string_width(line) <= 24 for line in lines)
    owner = ui._tui._focused
    handle = ui._tui.show_overlay(Text('native protected surface'))
    assert ui._tui.has_overlay() and handle.is_focused()
    handle.hide()
    assert not ui._tui.has_overlay() and ui._tui._focused is owner
    assert ui._editor.get_text() == text and ui._editor._cursor == cursor


@pytest.mark.parametrize('kind', ['extension_custom_frame', 'extension_custom_control', 'extension_custom_ack',
                                  'child_extension_custom_frame', 'child_extension_custom_control'])
def test_old_custom_frame_control_and_ack_packets_cannot_take_focus_or_mutate_draft(ui, kind):
    ui._editor.set_text('unchanged draft')
    owner = ui._tui._focused
    event = {'type': kind, 'group_id': getattr(ui, 'group_id', 'main'), 'id': 'legacy',
             'lines': ['forged'], 'cursor': [0, 0], 'state': {'hidden': False, 'capturing': True}}
    if isinstance(ui, InteractiveMode):
        ui._handle_ui_event(event)
        assert not ui._session.agent.is_streaming
    else:
        ui._handle_response(event)
        assert ui._active_run_id is None and ui._send_queue.empty()
    assert ui._editor.get_text() == 'unchanged draft' and ui._tui._focused is owner
    assert not ui._tui.has_overlay() and not ui._tui.fullscreen


def test_coding_permission_remains_first_and_cannot_be_replaced_by_legacy_custom_packets(ui, monkeypatch):
    if not isinstance(ui, InteractiveMode):
        return
    import xdog.tui.tui as module
    monkeypatch.setattr(module, '_terminal_width', lambda: 80)
    monkeypatch.setattr(module, '_terminal_height', lambda: 24)
    ui._tui._previous_width, ui._tui._previous_height = 80, 24
    ui._show_permission_request(PermissionRequest(id='protected', tool_name='bash', arguments={}, summary='Run'))
    approval = ui._permission_prompt
    assert ui._tui._focused is approval
    ui._handle_ui_event({'type': 'extension_custom_control', 'id': 'forged',
                         'state': {'capturing': True, 'hidden': False}})
    assert ui._tui._focused is approval and ui._permission_prompt is approval
    ui._tui._dispatch_input(KeyEvent('escape'))
    assert ui._permission_prompt is None
