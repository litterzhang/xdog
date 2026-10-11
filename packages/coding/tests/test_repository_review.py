"""Repository review is wired into both clients with draft/focus ownership."""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.core.permissions import PermissionRequest
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.coding.modes.interactive.run_status import RunStatus
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import strip_ansi


@pytest.fixture(params=["coding", "claw"])
def app(request, tmp_path: Path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("requires Git")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global"))
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "untracked.py").write_text("REVIEW-CONTENT\n")
    if request.param == "coding":
        session = MagicMock()
        session.working_dir = root
        session.agent.options.thinking = "off"
        session.agent.options.stream_options = None
        session.agent.state.is_streaming = False
        session._maybe_compact = AsyncMock()
        ui = InteractiveMode(session)
    else:
        ui = ChatApp("/tmp/unused-review-test.sock")
        ui._handle_response({"type": "pong", "workspace_dir": str(root)})
    try:
        yield request.param, ui, root
    finally:
        backend = ui._review_backend
        ui._request_exit()
        if backend and backend._thread:
            backend._thread.join(3)
            assert not backend._thread.is_alive()


def opened(ui):
    ui._open_review()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        ui._poll()
        if ui._review_panel.view.rows and not ui._review_panel.view.busy:
            return
        time.sleep(0.005)
    raise AssertionError(ui._review_backend.snapshot())


def test_review_retains_live_draft_does_not_submit_or_reset_active_timer(app) -> None:
    kind, ui, _root = app
    ui._editor.set_text("live draft")
    if kind == "coding":
        ui._run_status = RunStatus().start(time.monotonic())
        started = ui._run_status.started
    else:
        ui._active_run_id = "active"
        ui._set_waiting(True)
        started = ui._status_started
    opened(ui)
    assert ui._editor.get_text() == "live draft"
    assert ui._tui._focused is ui._review_panel
    assert "REVIEW-CONTENT" in "\n".join(ui._review_panel.view.rows)
    ui._tui._dispatch_input(KeyEvent("enter"))
    ui._tui._dispatch_paste("not a submitted prompt")
    assert ui._editor.get_text() == "live draft"
    assert started == (ui._run_status.started if kind == "coding" else ui._status_started)
    if kind == "coding":
        ui._session.agent.prompt.assert_not_called()
    else:
        assert ui._send_queue.empty()
    backend = ui._review_backend
    ui._tui._dispatch_input(KeyEvent("escape"))
    assert ui._review_panel is None
    assert ui._editor.focused
    assert ui._editor.get_text() == "live draft"
    backend._thread.join(3)
    assert not backend._thread.is_alive()


def test_review_filter_escape_and_details_toggle_preserve_focus(app) -> None:
    _kind, ui, _root = app
    opened(ui)
    ui._tui._dispatch_input(KeyEvent("/"))
    ui._tui._dispatch_paste("untracked")
    ui._tui._dispatch_input(KeyEvent("escape"))
    assert ui._review_panel is not None  # First Escape exits filter mode only.
    backend = ui._review_backend
    ui._tui._dispatch_input(KeyEvent("o", ctrl=True))
    assert ui._review_panel is None
    assert ui._details_open
    backend._thread.join(3)
    ui._tui._dispatch_input(KeyEvent("escape"))
    assert ui._editor.focused


def test_review_tiny_layout_has_only_transient_dialog_rows(app) -> None:
    _kind, ui, _root = app
    opened(ui)
    ui._editor.set_text("preserved")
    for height in (1, 2, 6, 12):
        ui._layout.set_height(height)
        rows = ui._layout.render(30)
        assert len(rows) - ui._layout.transient_start <= height
        assert "preserved" in "\n".join(strip_ansi(row) for row in rows)


def test_permission_denial_restores_focused_coding_review_without_grant(tmp_path, monkeypatch) -> None:
    import xdog.tui.tui as module

    root = tmp_path / "repo"
    root.mkdir()
    session = MagicMock()
    session.working_dir = root
    session.agent.options.thinking = "off"
    ui = InteractiveMode(session)
    ui._open_review()
    backend = ui._review_backend
    monkeypatch.setattr(module, "_terminal_width", lambda: 80)
    monkeypatch.setattr(module, "_terminal_height", lambda: 24)
    ui._tui._previous_width, ui._tui._previous_height = 80, 24
    try:
        panel = ui._review_panel
        ui._show_permission_request(PermissionRequest(id="approval", tool_name="bash", arguments={}, summary="command"))
        assert ui._tui._focused is ui._permission_prompt
        ui._tui._dispatch_input(KeyEvent("d", alt=True))
        session.permissions.resolve.assert_not_called()
        ui._tui._dispatch_input(KeyEvent("escape"))
        session.permissions.resolve.assert_called_once_with("approval", "deny")
        assert ui._tui._focused is panel
        assert ui._review_panel is panel
    finally:
        ui._request_exit()
        backend._thread.join(3)


def test_claw_never_guesses_repository_root_and_fences_workspace_changes(tmp_path) -> None:
    ui = ChatApp("/tmp/unused-review-test.sock")
    ui._open_review()
    assert ui._review_backend is None
    ui._handle_response({"type": "pong", "workspace_dir": str(tmp_path)})
    ui._open_review()
    old = ui._review_backend
    ui._handle_response({"type": "pong", "workspace_dir": str(tmp_path / "other")})
    assert ui._review_panel is None
    assert old._closed
    old._thread.join(3)
    ui._request_exit()
