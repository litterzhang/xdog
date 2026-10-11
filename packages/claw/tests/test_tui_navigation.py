from __future__ import annotations

from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.tui.keys import KeyEvent


def test_browser_results_are_provider_and_connection_fenced_and_do_not_change_active_run():
    app = ChatApp("/tmp/unused-nav.sock")
    app._handle_response({"type": "pong", "session_controls": 1, "session_id": "first"})
    app._active_run_id = "active"
    app._set_waiting(True)
    started = app._status_started
    app._editor.set_text("live draft")
    app._open_navigation()
    request = app._send_queue.get_nowait()
    app._handle_response({"type": "session_result", "request_id": request["request_id"], "success": True,
                          "scope": "sessions", "total": 1, "entries": [
                              {"id": "second", "title": "TARGET", "session_id": "second"},
                          ]})
    app._poll()
    assert app._navigation_panel._view.entries[0].session_id == "second"
    app._tui._dispatch_input(KeyEvent("enter"))
    assert app._send_queue.empty()  # Busy activation was refused, not queued behind a run.
    assert app._editor.get_text() == "live draft"
    assert app._status_started == started
    old = app._navigation_provider
    app._close_navigation()
    app._open_navigation()
    app._handle_response({"type": "session_result", "request_id": request["request_id"], "success": True,
                          "entries": [{"id": "stale", "title": "stale"}]})
    assert app._navigation_provider is not old
    assert not app._navigation_provider.snapshot().entries
    app._request_exit()


def test_successful_navigation_replays_context_without_losing_draft_or_sending_until_history_end():
    app = ChatApp("/tmp/unused-nav.sock")
    app._handle_response({"type": "pong", "session_controls": 1, "session_id": "first"})
    app._editor.set_text("preserved draft")
    app._navigate_session("resume", "second")
    request = app._send_queue.get_nowait()
    assert request["expected_session"] == "first"
    app._handle_response({"type": "session_result", "request_id": request["request_id"], "success": True})
    app._handle_response({"type": "session_changed", "request_id": request["request_id"], "session_id": "second",
                          "history_count": 1, "history_format": 2})
    assert not app._connection_ready
    assert app._pending_command is not None
    app._handle_response({"type": "history_chunk", "entries": [{"role": "user", "content": "RESTORED-TEXT"}]})
    app._handle_response({"type": "history_end"})
    assert app._connection_ready
    assert app._pending_command is None
    assert app._state["session_id"] == "second"
    assert app._editor.get_text() == "preserved draft"
    assert "RESTORED-TEXT" in "\n".join(app._chat_log.render(80))
    assert app._send_queue.empty()
    app._request_exit()


def test_legacy_gateway_idle_rejection_and_uncertain_mutation_do_not_replay():
    app = ChatApp("/tmp/unused-nav.sock")
    app._handle_response({"type": "pong"})
    app._navigate_session("new")
    assert app._send_queue.empty()
    app._handle_response({"type": "pong", "session_controls": 1})
    app._editor.set_text("live draft")
    app._navigate_session("new")
    request = app._send_queue.get_nowait()
    assert request["expected_session"] == ""  # Never send the synthetic legacy display UUID.
    app._handle_response({"type": "internal_error", "message": "lost reply"})
    app._handle_response({"type": "_connection_opened", "generation": 1})
    app._handle_response({"type": "pong", "session_controls": 1, "session_id": "actual-gateway-session"})
    assert app._send_queue.empty()
    assert app._editor.get_text() == "live draft"
    app._request_exit()
