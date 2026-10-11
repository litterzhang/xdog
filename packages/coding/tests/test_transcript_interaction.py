from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.core.permissions import PermissionRequest
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent
from xdog.tui.transcript_browser import TranscriptBrowser


def settle(ui):
    panel = ui._transcript_panel
    deadline = time.monotonic() + 3
    while panel.browser.snapshot().busy and time.monotonic() < deadline:
        ui._poll()
        time.sleep(0.002)
    ui._poll()
    assert not panel._view.busy and not panel._view.error
    return panel


@pytest.mark.parametrize("kind", ["coding", "claw"])
def test_live_transcript_sources_search_hidden_tool_reasoning_and_preserve_draft(kind, tmp_path):
    session = MagicMock()
    session.working_dir = tmp_path
    ui = InteractiveMode(session) if kind == "coding" else ChatApp("/tmp/unused-transcript.sock")
    try:
        ui._chat_log.add_user("actual user")
        ui._chat_log.add_assistant("actual answer", thinking="full reasoning NEEDLE")
        if kind == "coding":
            tool = ui._chat_log.add_tool("bash", {"command": "printf output"})
        else:
            tool = ui._chat_log.add_tool("bash", {"command": "printf output"}, tool_call_id="tool")
        tool.set_result("\n".join(f"hidden-{i}" for i in range(400)) + "\nTOOL-NEEDLE")
        ui._editor.set_text("unchanged draft")
        ui._editor.handle_input(KeyEvent("left"))
        cursor = ui._editor._cursor
        ui._tui._dispatch_input(KeyEvent("h", alt=True))
        panel = settle(ui)
        assert {record.role for record in panel.records} >= {"user", "assistant", "reasoning", "tool"}
        panel.search("needle")
        settle(ui)
        assert panel._view.matches == 2
        ui._tui._dispatch_input(KeyEvent("n"))
        settle(ui)
        assert panel._view.title.startswith("tool:")
        ui._tui._dispatch_input(KeyEvent("a"))
        settle(ui)
        selected = TranscriptBrowser.selected_text(panel.records, *panel.selection)
        assert "TOOL-NEEDLE" in selected and "hidden-399" in selected
        ui._tui._dispatch_input(KeyEvent("enter"))
        assert ui._tui._focused is panel
        assert ui._editor.get_text() == "unchanged draft" and ui._editor._cursor == cursor
        if kind == "coding":
            session.agent.prompt.assert_not_called()
        else:
            assert ui._send_queue.empty()
        ui._tui._dispatch_input(KeyEvent("escape"))  # selection
        settle(ui)
        ui._tui._dispatch_input(KeyEvent("escape"))  # close
        assert ui._transcript_panel is None and ui._tui._focused is ui._editor
        assert ui._editor._cursor == cursor
    finally:
        ui._request_exit()


def test_coding_permission_preempts_viewer_and_denial_restores_focus(tmp_path, monkeypatch):
    import xdog.tui.tui as module

    session = MagicMock()
    session.working_dir = tmp_path
    ui = InteractiveMode(session)
    monkeypatch.setattr(module, "_terminal_width", lambda: 80)
    monkeypatch.setattr(module, "_terminal_height", lambda: 24)
    ui._tui._previous_width, ui._tui._previous_height = 80, 24
    try:
        ui._chat_log.add_user("context")
        ui._open_transcript()
        panel = settle(ui)
        ui._show_permission_request(PermissionRequest(id="approval", tool_name="bash", arguments={}, summary="cmd"))
        assert ui._tui._focused is ui._permission_prompt
        ui._tui._dispatch_input(KeyEvent("h", alt=True))
        session.permissions.resolve.assert_not_called()
        ui._tui._dispatch_input(KeyEvent("escape"))
        session.permissions.resolve.assert_called_once_with("approval", "deny")
        assert ui._tui._focused is panel and ui._transcript_panel is panel
    finally:
        ui._request_exit()
