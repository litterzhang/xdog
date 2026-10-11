from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp, CustomEditor
from xdog.tui.components.prompt_editor import PromptEditor
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import strip_ansi, visible_width


def _connected_app() -> ChatApp:
    app = ChatApp("/tmp/unused-claw-test.sock")
    app._handle_response({"type": "pong", "session_id": "test-session", "history_count": 0})
    return app


def _plain(lines: list[str]) -> str:
    return "\n".join(strip_ansi(line) for line in lines)


def _submit(editor: CustomEditor, text: str) -> None:
    editor.set_text(text)
    assert editor.handle_input(KeyEvent(key="enter"))


@pytest.mark.parametrize("outcome", ["final", "error", "aborted", "internal_error"])
def test_terminal_run_events_clear_status_timer(outcome: str) -> None:
    app = _connected_app()
    _submit(app._editor, "request")
    app._recv_queue.put({"type": outcome, "run_id": app._active_run_id})
    app._poll()
    assert app._status_started is None
    assert not app._waiting
    assert app._active_run_id is None
    assert "idle" in _plain(app._footer.render(100))


def test_cancel_status_stops_timer_and_idle_repaints() -> None:
    app = _connected_app()
    _submit(app._editor, "request")
    app._handle_escape()
    app._tui._render_requested = False
    for _ in range(3):
        app._poll()
    assert app._status_started is None
    assert "cancelling" in _plain(app._footer.render(100))
    assert not app._tui._render_requested


def test_busy_poll_only_repaints_when_status_changes(monkeypatch) -> None:
    monkeypatch.setattr("xdog.claw.channels.tui.tui_app.time.monotonic", lambda: 100.0)
    app = _connected_app()
    _submit(app._editor, "request")
    app._poll()  # Render the submitted request's token estimate.
    app._tui._render_requested = False
    app._poll()
    assert not app._tui._render_requested
    monkeypatch.setattr("xdog.claw.channels.tui.tui_app.time.monotonic", lambda: 101.0)
    app._poll()
    assert app._tui._render_requested


def test_cancelled_io_worker_clears_busy_status_and_restores_draft(monkeypatch) -> None:
    app = _connected_app()
    _submit(app._editor, "active")
    _submit(app._editor, "queued")
    app._editor.set_text("draft")
    monkeypatch.setattr(app, "_io_loop_async", AsyncMock(side_effect=asyncio.CancelledError))
    app._io_loop()
    app._poll()
    assert app._status_started is None
    assert not app._waiting
    assert app._active_run_id is None
    assert app._editor.get_text() == "active\n\nqueued\n\ndraft"


def test_claw_autocomplete_arrow_release_does_not_move_twice() -> None:
    from xdog.tui.stdin_buffer import KeyBytes

    app = _connected_app()
    app._editor.set_text("/")
    app._tui._handle_frame(KeyBytes(b"\x1b[1;1:1B\x1b[1;1:3B"))
    first = _plain(app._editor.render(100))
    app._tui._handle_frame(KeyBytes(b"\x1b[1;1:1A\x1b[1;1:3A"))
    app._tui._handle_frame(KeyBytes(b"\x1b[B"))
    assert _plain(app._editor.render(100)) == first


def test_claw_editor_is_thin_shared_editor_with_unicode_paste_and_budget() -> None:
    editor = CustomEditor()

    assert isinstance(editor, PromptEditor)
    editor.set_text("a👨\u200d👩\u200d👧\u200d👦b")
    editor.handle_input(KeyEvent(key="left"))
    editor.handle_input(KeyEvent(key="left"))
    editor.handle_input(KeyEvent(key="delete"))
    assert editor.get_text() == "ab"

    editor.set_text("a")
    assert editor.handle_paste("👩💻\r\n\x1b[31mred\x1b[0m\x00")
    assert editor.get_text() == "a👩💻\nred"
    editor.set_render_budget(4, show_borders=True)
    editor.set_text("\n".join(f"line-{index}" for index in range(12)))
    rendered = editor.render(20)
    assert len(rendered) == 4
    assert "line-11" in _plain(rendered)


def test_claw_uses_bounded_inline_tail_and_single_truncated_status() -> None:
    app = _connected_app()
    app._layout.set_height(8)
    app._render_todos([
        {"content": f"todo-{index}", "status": "pending"}
        for index in range(20)
    ])
    app._render_goal({
        "id": "goal-1",
        "title": "a deliberately long active goal",
        "status": "active",
        "tasks": [
            {"description": f"task-{index}", "status": "pending"}
            for index in range(20)
        ],
    })
    app._update_footer()

    rows = app._layout.render(32)
    tail = rows[app._layout.transient_start:]
    status_rows = app._footer.render(32)

    assert len(tail) <= 8
    assert len(status_rows) == 1
    assert visible_width(status_rows[0]) <= 32
    assert app._header not in app._layout.children
    assert app._status_container not in app._layout.children


def test_busy_restores_active_queue_and_new_draft_once() -> None:
    app = _connected_app()
    _submit(app._editor, "active request")
    run_id = app._active_run_id
    assert run_id is not None
    _submit(app._editor, "queued request")
    app._editor.set_text("new draft")

    app._handle_response({"type": "busy", "run_id": run_id})
    restored = app._editor.get_text()
    app._handle_response({"type": "busy", "run_id": run_id})

    assert restored == "active request\n\nqueued request\n\nnew draft"
    assert app._editor.get_text() == restored
    assert not app._pending_messages
    assert app._active_run_id is None


def test_cancel_restores_drafts_and_suppresses_late_run_content_until_terminal() -> None:
    app = _connected_app()
    _submit(app._editor, "active request")
    run_id = app._active_run_id
    assert run_id is not None
    _submit(app._editor, "queued request")
    app._editor.set_text("new draft")

    app._handle_escape()
    restored = app._editor.get_text()
    app._handle_response({"type": "delta", "run_id": run_id, "content": "LATE-DELTA"})
    app._handle_response({"type": "final", "run_id": run_id, "content": "LATE-FINAL"})

    assert restored == "active request\n\nqueued request\n\nnew draft"
    assert app._editor.get_text() == restored
    assert "LATE-DELTA" not in _plain(app._chat_log.render(100))
    assert "LATE-FINAL" not in _plain(app._chat_log.render(100))
    assert app._active_run_id is None


def test_ctrl_o_opens_bounded_latest_details_and_escape_closes_before_cancel() -> None:
    app = _connected_app()
    app._active_run_id = "run-1"
    app._active_text = "request"
    app._handle_response({
        "type": "tool_call",
        "run_id": "run-1",
        "id": "call-1",
        "name": "bash",
        "arguments": {"command": "pytest -q"},
    })
    app._handle_response({
        "type": "tool_result",
        "run_id": "run-1",
        "id": "call-1",
        "name": "bash",
        "result": "\n".join(["first", *(f"line-{index}" for index in range(20)), "DETAIL-TAIL"]),
    })

    assert app._handle_global_input(KeyEvent(key="o", ctrl=True)) == {"consume": True}
    assert app._layout.details is app._details_panel
    details = app._details_panel.render(60)
    assert len(details) <= 8
    assert "call-1" in _plain(details)
    assert "first" in _plain(details)
    assert "DETAIL-TAIL" not in _plain(details)
    app._details_panel.handle_input(KeyEvent(key="end"))
    assert "DETAIL-TAIL" in _plain(app._details_panel.render(60))
    assert "DETAIL-TAIL" not in _plain(app._chat_log.render(100))

    assert app._tui._dispatch_input(KeyEvent(key="escape"))
    assert app._layout.details is None
    assert app._active_run_id == "run-1"
    assert app._handle_global_input(KeyEvent(key="escape")) == {"consume": True}
    abort = app._send_queue.get_nowait()
    assert abort["type"] == "abort"
    assert abort["run_id"] == "run-1"


def test_submit_while_abort_settles_remains_editable() -> None:
    app = _connected_app()
    _submit(app._editor, "active")
    run_id = app._active_run_id
    app._handle_escape()
    _submit(app._editor, "new during cancel")
    app._handle_response({"type": "aborted", "run_id": run_id})
    assert app._editor.get_text() == "new during cancel"
    assert not app._pending_messages


def test_stale_work_summary_cannot_overwrite_current_run() -> None:
    app = _connected_app()
    app._active_run_id = "current"
    app._handle_response({
        "type": "todo", "run_id": "old",
        "todos": [{"content": "STALE", "status": "pending"}],
    })
    assert app._last_todos == []


def test_transport_failure_restores_active_and_queued_before_live_draft() -> None:
    app = _connected_app()
    _submit(app._editor, "active")
    _submit(app._editor, "queued")
    app._editor.set_text("new draft")
    failure = {"type": "internal_error", "message": "connection lost"}
    app._handle_response(failure)
    app._handle_response(failure)
    assert app._editor.get_text() == "active\n\nqueued\n\nnew draft"
    assert not app._pending_messages
    assert app._active_run_id is None


def test_claw_streaming_preview_shows_tail_and_keeps_full_details() -> None:
    from xdog.claw.channels.tui.tui_app import ToolMessage
    tool = ToolMessage("bash", {})
    output = "\n".join(["FIRST"] + ["middle"] * 20 + ["LATEST"])
    tool.set_streaming(output)
    assert "LATEST" in _plain(tool.render(80))
    assert "FIRST" not in _plain(tool.render(80))
    assert "hidden" in _plain(tool.render(80))
    assert tool.detail_body == output


def test_claw_idle_poll_and_queue_update_have_bounded_frames(monkeypatch) -> None:
    import io

    import xdog.tui.tui as module
    output = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", output)
    monkeypatch.setattr(module, "_terminal_width", lambda: 80)
    monkeypatch.setattr(module, "_terminal_height", lambda: 24)
    app = _connected_app()
    app._tui._do_render()
    output.seek(0)
    output.truncate()
    for _ in range(20):
        app._poll()
        app._tui._do_render()
    assert output.getvalue() == ""
    app._pending_messages.append("queued")
    app._update_queue_summary()
    app._tui._do_render()
    assert output.getvalue().count("\x1b[?2026h") == 1
    assert output.getvalue().count("\x1b[?2026l") == 1


def test_elapsed_survives_busy_phase_changes_and_wall_clock_jump(monkeypatch):
    app = _connected_app()
    monkeypatch.setattr("xdog.claw.channels.tui.tui_app.time.monotonic", lambda: 100.0)
    monkeypatch.setattr("xdog.claw.channels.tui.tui_app.time.time", lambda: 1000.0)
    app._set_activity_status("waiting")
    monkeypatch.setattr("xdog.claw.channels.tui.tui_app.time.monotonic", lambda: 107.0)
    monkeypatch.setattr("xdog.claw.channels.tui.tui_app.time.time", lambda: 1.0)
    app._set_activity_status("streaming")
    assert app._format_elapsed() == "7s"
    app._set_activity_status("idle")
    assert app._status_started is None
    app._set_activity_status("waiting")
    assert app._format_elapsed() == "0s"


def test_claw_contextual_hints_follow_details_focus_and_height():
    app = _connected_app()
    assert "Ctrl+Enter newline" in _plain(app._layout.render(100))
    app._handle_global_input(KeyEvent(key="o", ctrl=True))
    assert "Details focused" in _plain(app._layout.render(100))
    app._close_details()
    assert "Ctrl+Enter newline" in _plain(app._layout.render(100))
    app._layout.set_height(4)
    assert "Ctrl+Enter newline" not in _plain(app._layout.render(100))


def test_streaming_event_flood_yields_to_input_and_preserves_fifo(monkeypatch) -> None:
    app = _connected_app()
    monkeypatch.setattr("xdog.tui.event_queue.time.monotonic", lambda: 100.0)
    seen = []
    monkeypatch.setattr(app, "_handle_response", lambda event: seen.append(event["sequence"]))
    for sequence in range(130):
        app._recv_queue.put({"sequence": sequence})
    app._poll()
    assert seen == list(range(64))
    assert app._tui._render_requested
    app._tui._dispatch_input(KeyEvent(key="x"))
    assert app._editor.get_text() == "x"
    app._poll()
    app._poll()
    assert seen == list(range(130))


def test_claw_file_completion_uses_gateway_workspace_and_reconnect_fences_results(tmp_path) -> None:
    import time

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "review-old.py").touch()
    (second / "review-new.py").touch()
    app = ChatApp("/tmp/unused-claw-test.sock")
    assert app._file_completions is None  # Client launch cwd is not the group workspace.
    app._handle_response({"type": "pong", "workspace_dir": str(first)})
    old = app._file_completions
    app._editor.set_text("Read @rev")
    app._handle_response({"type": "pong", "workspace_dir": str(second)})
    assert old is not None and old._closed
    assert app._file_completions is not None and app._file_completions.root == second
    deadline = time.monotonic() + 3
    try:
        while time.monotonic() < deadline and app._editor._select_list is None:
            time.sleep(0.005)
            app._poll()
        assert app._editor._select_list is not None
        assert app._editor._select_list.selected_value == "@review-new.py"
        app._editor.handle_input(KeyEvent(key="enter"))
        assert app._editor.get_text() == "Read @review-new.py"
        assert app._active_run_id is None
    finally:
        app._request_exit()
    assert app._file_completions._closed


def test_claw_legacy_peer_does_not_keep_prior_workspace_completion(tmp_path) -> None:
    app = ChatApp("/tmp/unused-claw-test.sock")
    app._handle_response({"type": "pong", "workspace_dir": str(tmp_path)})
    old = app._file_completions
    app._handle_response({"type": "pong"})
    assert old is not None and old._closed
    assert app._file_completions is None
    assert not app._editor.autocomplete_active
    app._request_exit()


def test_background_file_completion_cannot_hide_focused_claw_details() -> None:
    from xdog.tui.autocomplete import CompletionItem, StaticCompletionProvider

    app = _connected_app()
    app._chat_log.add_assistant("answer", thinking="DETAIL-MARKER")
    app._handle_global_input(KeyEvent(key="o", ctrl=True))
    app._editor.set_autocomplete_provider(StaticCompletionProvider([CompletionItem("@one.py")]))
    app._editor.set_text("Read @o")
    assert "DETAIL-MARKER" in _plain(app._layout.render(80))
    assert "@one.py" not in _plain(app._layout.render(80))
    assert app._handle_global_input(KeyEvent(key="escape")) == {"consume": True}
    assert not app._details_open
    assert app._editor.focused
    assert "@one.py" in _plain(app._layout.render(80))
    app._request_exit()
