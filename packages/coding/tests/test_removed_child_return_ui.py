"""UI018 removes live-image return callbacks, not text/stopped recovery or backend tools."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('action', ['return_prepare', 'return_commit', 'return_cancel'])
def test_retired_live_return_callbacks_do_not_touch_successor_panel_or_dispatch_work(
        coding_session, tmp_path, monkeypatch, host, action):
    monkeypatch.setenv('CODING_DIR', str(tmp_path))
    monkeypatch.setenv('CLAW_DIR', str(tmp_path))
    ui = InteractiveMode(coding_session) if host == 'coding' else ChatApp('/unused')
    failures = []
    successor = SimpleNamespace(fail=failures.append)
    ui._child_panel = successor
    ui._child_context = 'newer-owner'
    ui._editor.set_text('UNSENT PARENT')
    try:
        ui._child_send('queue', 'old-child', json.dumps({'action': action}), 'read-only')
        assert ui._child_panel is successor and ui._child_context == 'newer-owner' and not failures
        assert ui._editor.get_text() == 'UNSENT PARENT'
        if host == 'coding':
            assert not ui._child_jobs and not ui._session.agent.is_streaming
        else:
            assert not ui._children_pending and ui._send_queue.empty()
            assert not hasattr(ui, '_child_return_supported') and not hasattr(ui, '_child_return_owner')
    finally:
        ui._child_panel = None
        ui._request_exit()


def test_old_live_return_result_cannot_settle_a_current_claw_queue_request():
    ui = ChatApp('/unused')
    ui._gateway_session_id = 'current-session'
    ui._children_pending['same-request'] = 'queue', 'current-session'
    ui._child_ui_requests.add('same-request')
    ui._child_queue_owners['same-request'] = 'current-queue'
    ui._handle_response({'type': 'child_result', 'request_id': 'same-request', 'group_id': ui.group_id,
                         'session_id': 'current-session', 'child_return_owner': 'retired', 'queue_view': 'retired',
                         'success': True, 'data': {'queue': {'return_prepared': {}}}})
    assert ui._children_pending == {'same-request': ('queue', 'current-session')}
    assert ui._child_ui_requests == {'same-request'}
    assert ui._child_queue_owners == {'same-request': 'current-queue'}
    assert ui._send_queue.empty() and ui._active_run_id is None
