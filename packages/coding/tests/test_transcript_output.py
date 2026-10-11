from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.core.permissions import PermissionRequest
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent
from xdog.tui.transcript_browser import TranscriptRecord

RECORDS = (TranscriptRecord("u", "user", "input", "password=PRIVATE-OUTPUT-SECRET"),
           TranscriptRecord("a", "assistant", "response", "exact answer\n" + "x" * 50000 + "OUTPUT-TAIL"))


def coding(tmp_path):
    session = MagicMock()
    session.working_dir = tmp_path
    session.context_limit = 100000
    session.session_id = "session"
    ui = InteractiveMode(session)
    ui._chat_log.add_user(RECORDS[0].body)
    ui._chat_log.add_assistant(RECORDS[1].body)
    return ui


def wait(ui, predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        ui._poll()
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("output did not settle")


def resolve_source(ui):
    request = ui._send_queue.get_nowait()
    if request["type"] == "source_cancel":
        request = ui._send_queue.get_nowait()
    assert request["type"] == "source_read"
    ui._handle_response(dict(type="source_ready", request_id=request["request_id"], group_id="main",
                             session_id="session", records=RECORDS))
    return request


@pytest.mark.parametrize("kind", ["coding", "claw"])
def test_live_private_export_and_share_require_review_preserve_draft(kind, tmp_path):
    ui = coding(tmp_path) if kind == "coding" else ChatApp("/tmp/unused-output.sock")
    if kind == "claw":
        ui._handle_response({"type": "pong", "session_id": "session", "transcript_source": 1,
                             "workspace_dir": str(tmp_path)})
    try:
        ui._editor.set_text("unchanged draft")
        ui._editor.handle_input(KeyEvent("left"))
        cursor = ui._editor._cursor
        target = tmp_path / "snapshot.json"
        ui._output_command("export", f"{target} json all")
        if kind == "claw":
            resolve_source(ui)
        wait(ui, lambda: target.exists() and not ui._outputs.pending)
        assert "OUTPUT-TAIL" in target.read_text() and "PRIVATE-OUTPUT-SECRET" in target.read_text()
        assert ui._editor.get_text() == "unchanged draft" and ui._editor._cursor == cursor
        shared = tmp_path / "share.html"
        ui._output_command("share", str(shared))
        if kind == "claw":
            resolve_source(ui)
        wait(ui, lambda: ui._outputs.panel is not None)
        panel = ui._outputs.panel
        assert not shared.exists() and ui._tui._focused is panel
        panel.set_render_budget(12)
        wait(ui, lambda: (panel.render(160) and panel._visible))
        ui._tui._previous_width, ui._tui._previous_height = 160, 24
        # Invoke the owned panel directly; terminal geometry is verified by PTY tests.
        panel.handle_input(KeyEvent("enter"))
        wait(ui, lambda: shared.exists() and not ui._outputs.pending)
        assert "PRIVATE-OUTPUT-SECRET" not in shared.read_text()
        assert "&lt;redacted&gt;" in shared.read_text()
        assert ui._editor.get_text() == "unchanged draft" and ui._editor._cursor == cursor
        if kind == "coding":
            ui._session.agent.prompt.assert_not_called()
        else:
            assert all(item["type"] in {"source_read", "source_cancel"} for item in list(ui._send_queue.queue))
    finally:
        ui._request_exit()


def test_claw_failed_refresh_never_exports_old_snapshot(tmp_path):
    ui = ChatApp("/tmp/unused-output.sock")
    try:
        ui._handle_response({"type": "pong", "session_id": "session", "transcript_source": 1})
        ui._open_transcript()
        resolve_source(ui)
        wait(ui, lambda: ui._transcript_panel.settled)
        target = tmp_path / "not-created.txt"
        ui._output_command("export", f"{target} text response")
        request = ui._send_queue.get_nowait()
        ui._handle_response(dict(type="source_failed", request_id=request["request_id"], session_id="session",
                                 group_id="main", error="lost"))
        ui._poll()
        assert ui._pending_output is None and not target.exists()
        assert not ui._outputs.pending
    finally:
        ui._request_exit()


def test_tool_approval_preempts_share_review_without_grant_or_save(tmp_path, monkeypatch):
    import xdog.tui.tui as module

    ui = coding(tmp_path)
    target = tmp_path / "share.html"
    monkeypatch.setattr(module, "_terminal_width", lambda: 120)
    monkeypatch.setattr(module, "_terminal_height", lambda: 24)
    ui._tui._previous_width, ui._tui._previous_height = 120, 24
    try:
        ui._output_command("share", str(target))
        wait(ui, lambda: ui._outputs.panel is not None)
        panel = ui._outputs.panel
        ui._show_permission_request(PermissionRequest(id="approval", tool_name="bash", arguments={}, summary="cmd"))
        ui._poll()
        assert ui._tui._focused is ui._permission_prompt
        ui._tui._dispatch_input(KeyEvent("c", alt=True))
        ui._session.permissions.resolve.assert_not_called()
        assert not target.exists()
        ui._tui._dispatch_input(KeyEvent("escape"))
        ui._session.permissions.resolve.assert_called_once_with("approval", "deny")
        ui._poll()
        assert ui._tui._focused is panel and not target.exists()
        panel.handle_input(KeyEvent("escape"))
        ui._poll()
        assert ui._outputs.panel is None and not target.exists()
    finally:
        ui._request_exit()
