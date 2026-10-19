"""Claw history inspection never selects an alternate execution route."""
from __future__ import annotations

import pytest
from xdog.claw.channels.tui.tui_app import SLASH_COMMANDS, ChatApp
from xdog.tui.keys import KeyEvent


def test_history_browser_is_read_only_even_while_main_agent_is_busy():
    app = ChatApp("/tmp/unused-nav.sock")
    try:
        app._handle_response({"type": "pong", "session_controls": 1, "session_id": "first",
                              "saved_source": 1, "transcript_source": 1})
        app._active_run_id = "active"
        app._set_waiting(True)
        started = app._status_started
        app._editor.set_text("live draft")
        app._open_navigation()
        request = app._send_queue.get_nowait()
        app._handle_response({"type": "session_result", "request_id": request["request_id"], "success": True,
                              "scope": "sessions", "total": 1, "entries": [
                                  {"id": "second", "kind": "session", "title": "TARGET", "session_id": "second"},
                              ]})
        app._poll()
        panel = app._navigation_panel
        assert panel._view.entries[0].session_id == "second"
        assert "read-only" in panel.render(160)[0] and "activate" not in panel.render(160)[0]
        app._tui._dispatch_input(KeyEvent("n"))
        assert app._send_queue.empty()  # No new-context shortcut in history browsing.
        app._tui._dispatch_input(KeyEvent("enter"))
        assert app._saved_scope == ("second", "")
        assert app._navigation_panel is None and app._transcript_panel is not None
        assert app._editor.get_text() == "live draft"
        assert app._status_started == started and app._active_run_id == "active"
        assert app._gateway_session_id == "first"
        while not app._send_queue.empty():
            assert app._send_queue.get_nowait()["type"] != "session_control"
    finally:
        app._request_exit()


def test_browser_results_are_provider_and_connection_fenced():
    app = ChatApp("/tmp/unused-nav.sock")
    try:
        app._handle_response({"type": "pong", "session_controls": 1, "session_id": "first"})
        app._open_navigation()
        request = app._send_queue.get_nowait()
        old = app._navigation_provider
        app._close_navigation()
        app._open_navigation()
        app._handle_response({"type": "session_result", "request_id": request["request_id"], "success": True,
                              "entries": [{"id": "stale", "title": "stale"}]})
        assert app._navigation_provider is not old
        assert not app._navigation_provider.snapshot().entries
    finally:
        app._request_exit()


def test_new_context_sync_keeps_draft_and_waits_for_history_end():
    app = ChatApp("/tmp/unused-nav.sock")
    try:
        app._handle_response({"type": "pong", "session_controls": 1, "session_id": "first"})
        app._editor.set_text("preserved draft")
        app._new_context()
        request = app._send_queue.get_nowait()
        assert request["expected_session"] == "first" and request["action"] == "new"
        assert not {"session_id", "branch_id", "index"} & request.keys()
        app._handle_response({"type": "session_result", "request_id": request["request_id"], "success": True})
        app._handle_response({"type": "session_changed", "request_id": request["request_id"], "session_id": "second",
                              "history_count": 1, "history_format": 2})
        assert not app._connection_ready and app._pending_command is not None
        app._handle_response({"type": "history_chunk", "entries": [{"role": "user", "content": "REPLAY-TEXT"}]})
        app._handle_response({"type": "history_end"})
        assert app._connection_ready and app._pending_command is None
        assert app._state["session_id"] == "second"
        assert app._editor.get_text() == "preserved draft"
        assert "REPLAY-TEXT" in "\n".join(app._chat_log.render(80))
        assert app._send_queue.empty()
    finally:
        app._request_exit()


@pytest.mark.parametrize('command', ['/resume old', '/fork', '/branch old', '/goto 1'])
def test_retired_navigation_commands_never_send_work_or_model_input(command):
    app = ChatApp('/tmp/unused-nav.sock')
    try:
        app._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'first'})
        app._handle_submit(command)
        assert app._send_queue.empty()
        assert command.split()[0] not in SLASH_COMMANDS
        assert 'read-only' in '\n'.join(app._chat_log.render(120))
        assert not hasattr(app, '_navigate_session')
    finally:
        app._request_exit()


def test_legacy_gateway_idle_rejection_and_uncertain_rotation_do_not_replay():
    app = ChatApp("/tmp/unused-nav.sock")
    try:
        app._handle_response({"type": "pong"})
        app._new_context()
        assert app._send_queue.empty()
        app._handle_response({"type": "pong", "session_controls": 1})
        app._editor.set_text("live draft")
        app._new_context()
        request = app._send_queue.get_nowait()
        assert request["expected_session"] == ""
        app._handle_response({"type": "internal_error", "message": "lost reply"})
        app._handle_response({"type": "_connection_opened", "generation": 1})
        app._handle_response({"type": "pong", "session_controls": 1, "session_id": "actual-gateway-context"})
        assert app._send_queue.empty() and app._editor.get_text() == "live draft"
    finally:
        app._request_exit()


def test_reconnect_synchronizes_only_current_group_context_without_history_activation():
    app = ChatApp('/tmp/unused-nav.sock')
    try:
        app._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'old'})
        app._chat_log.add_system('OLD-CONTEXT-ONLY')
        app._editor.set_text('Preserve unsent draft')
        app._handle_response({'type': '_connection_opened', 'generation': 1})
        app._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'current',
                              'history_count': 1, 'history_format': 2})
        assert not app._connection_ready
        app._handle_response({'type': 'history_chunk', 'entries': [{'role': 'user', 'content': 'CURRENT-CONTEXT'}]})
        app._handle_response({'type': 'history_end'})
        rendered = '\n'.join(app._chat_log.render(100))
        assert app._connection_ready and not app._session_context_stale
        assert app._gateway_session_id == 'current' and app._editor.get_text() == 'Preserve unsent draft'
        assert 'CURRENT-CONTEXT' in rendered and 'OLD-CONTEXT-ONLY' not in rendered
        assert app._send_queue.empty()
    finally:
        app._request_exit()
