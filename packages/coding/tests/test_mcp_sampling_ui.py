"""Removed Sampling commands/events cannot start models or replace retained MCP review."""
from __future__ import annotations

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.components.mcp_panel import McpPanel


@pytest.fixture(params=['coding', 'claw'])
def ui(request, coding_session, tmp_path, monkeypatch):
    monkeypatch.setenv('CODING_DIR', str(tmp_path))
    monkeypatch.setenv('CLAW_DIR', str(tmp_path))
    app = InteractiveMode(coding_session) if request.param == 'coding' else ChatApp('/unused')
    app._mcp_panel = McpPanel(lambda *args: None, lambda: None, lambda: None, lambda: None)
    app._editor.set_text('UNSENT PARENT')
    try:
        yield app
    finally:
        app._request_exit()


@pytest.mark.parametrize('action', [
    'sampling_on', 'sampling_tasks_on', 'sampling_off', 'sampling_answer',
    'elicitation_on', 'elicitation_tasks_on', 'elicitation_off', 'elicitation_answer', 'elicitation_open',
    'roots_prepare', 'roots_commit', 'roots_cancel',
])
def test_historical_sampling_actions_do_not_create_worker_model_or_transport_work(ui, action):
    for name in ('_sampling_enable', '_sampling_off', '_sampling_bindings', '_sampling_execution_lock',
                 '_mcp_sampling_supported', '_mcp_sampling_servers', '_mcp_sampling_requests'):
        assert not hasattr(ui, name)
    ui._mcp_panel._busy = True
    ui._mcp_panel._message = 'newer unrelated request still owns this view'
    current = ui._mcp_active, ui._mcp_owner_context, ui._mcp_panel
    ui._mcp_send(action, 'retired-owner', '{}', {})
    assert ui._mcp_panel._busy is True
    assert ui._mcp_panel._message == 'newer unrelated request still owns this view'
    assert (ui._mcp_active, ui._mcp_owner_context, ui._mcp_panel) == current
    assert not hasattr(ui._mcp_panel, 'sampling_review')
    assert ui._editor.get_text() == 'UNSENT PARENT'
    if isinstance(ui, InteractiveMode):
        assert not ui._session.agent.is_streaming and not ui._session.messages
        assert ui._session_events.runtime is None
    else:
        assert ui._active_run_id is None and ui._send_queue.empty()


@pytest.mark.parametrize('kind', [
    'mcp_sampling_event', 'mcp_sampling_result', 'mcp_sampling_failed',
    'mcp_elicitation_event', 'mcp_elicitation_result', 'mcp_elicitation_failed', 'mcp_roots_result',
])
def test_historical_sampling_events_cannot_claim_focus_or_trigger_a_model(ui, kind):
    panel, owner = ui._mcp_panel, ui._tui._focused
    if isinstance(ui, InteractiveMode):
        ui._handle_ui_event({'type': kind})
        assert not ui._session.agent.is_streaming
    else:
        ui._handle_response({'type': kind})
        assert ui._active_run_id is None and ui._send_queue.empty()
    assert ui._mcp_panel is panel and ui._tui._focused is owner
    assert ui._editor.get_text() == 'UNSENT PARENT'
