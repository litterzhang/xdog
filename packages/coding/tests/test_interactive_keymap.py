"""Keymaps and external-edit handoffs are wired into both actual applications."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.core.permissions import PermissionRequest
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.external_editor import ExternalEditorResult
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.utils import strip_ansi


@pytest.fixture(params=["coding", "claw"])
def app(request, tmp_path: Path, monkeypatch):
    kind = request.param
    monkeypatch.setenv("CODING_DIR" if kind == "coding" else "CLAW_DIR", str(tmp_path))
    (tmp_path / "keybindings.json").write_text(json.dumps({
        "send": "ctrl+s", "newline": "alt+n", "details": "ctrl+t", "external_editor": "f4",
    }))
    if kind == "coding":
        session = MagicMock()
        session.agent.options.thinking = "off"
        session.agent.options.stream_options = None
        session.agent.state.model = "test/model"
        session.agent.state.is_streaming = False
        session.model = "test/model"
        session.session_id = "test-session"
        session.context_limit = 100_000
        session.working_dir = tmp_path
        instance = InteractiveMode(session)
    else:
        instance = ChatApp("/tmp/unused-keymap-test.sock")
        instance._handle_response({"type": "pong", "workspace_dir": str(tmp_path)})
    try:
        yield kind, instance
    finally:
        instance._request_exit()


def plain(component) -> str:
    return "\n".join(strip_ansi(line) for line in component.render(160))


def test_application_loads_custom_keys_and_updates_hints(app, monkeypatch) -> None:
    kind, ui = app
    assert "Ctrl+S send" in plain(ui._hints)
    assert "Alt+N newline" in plain(ui._hints)
    assert "Ctrl+T details" in plain(ui._hints)
    assert "F4 editor" in plain(ui._hints)
    assert ui._handle_global_input(KeyEvent("o", ctrl=True)) is None
    assert not ui._details_open
    assert ui._handle_global_input(KeyEvent("t", ctrl=True)) == {"consume": True}
    assert ui._details_open
    ui._handle_global_input(KeyEvent("t", ctrl=True))
    assert not ui._details_open
    submitted: list[str] = []
    ui._editor.on_submit = submitted.append
    ui._editor.set_text("draft")
    ui._tui._dispatch_input(KeyEvent("enter"))
    assert ui._editor.get_text() == "draft"
    ui._tui._dispatch_input(KeyEvent("s", ctrl=True))
    assert submitted == ["draft"]
    ui._show_keybindings()
    assert "Ctrl+S: send" in plain(ui._chat_log)


@pytest.mark.parametrize("text", [None, "edited\n多行", "original"])
def test_external_result_applies_only_to_draft_and_keeps_undo(app, monkeypatch, text) -> None:
    kind, ui = app
    module = "xdog.coding.modes.interactive.interactive_mode" if kind == "coding" else "xdog.claw.channels.tui.tui_app"
    callbacks = []
    monkeypatch.setattr(ui._tui, "run_outside_terminal", lambda callback: callbacks.append(callback) or True)
    calls = []

    def edit(original, **kwargs):
        calls.append((original, kwargs))
        assert ui._editor.get_text() == "original"
        return ExternalEditorResult(text=text, message="controlled-editor-return")

    monkeypatch.setattr(module + ".edit_draft", edit)
    ui._editor.set_text("original")
    ui._editor._cursor = 3
    ui._tui._dispatch_input(KeyEvent("f4"))
    assert len(callbacks) == 1
    assert not calls  # Scheduling never launches a child inside input dispatch.
    callbacks[0]()
    assert len(calls) == 1
    assert ui._editor.get_text() == (text if text is not None else "original")
    if text in {None, "original"}:
        assert ui._editor._cursor == 3
    else:
        ui._editor.handle_input(KeyEvent("z", alt=True))
        assert ui._editor.get_text() == "original"
        assert ui._editor._cursor == 3
    if kind == "coding":
        assert not ui._worker_active
        ui._session.agent.prompt.assert_not_called()
    else:
        assert ui._active_run_id is None
        assert ui._send_queue.empty()
    assert "controlled-editor-return" in plain(ui._chat_log)


def test_external_editor_unavailable_or_details_focused_preserves_draft(app, monkeypatch) -> None:
    _kind, ui = app
    calls = []
    monkeypatch.setattr(ui._tui, "run_outside_terminal", lambda callback: calls.append(callback) or False)
    ui._editor.set_text("original")
    ui._open_external_editor()
    assert ui._editor.get_text() == "original"
    assert len(calls) == 1
    ui._handle_global_input(KeyEvent("t", ctrl=True))
    ui._open_external_editor()
    assert len(calls) == 1
    assert ui._details_open
    assert ui._editor.get_text() == "original"


def test_repeat_cannot_open_editor_or_toggle_details(app, monkeypatch) -> None:
    _kind, ui = app
    calls = []
    monkeypatch.setattr(ui._tui, "run_outside_terminal", lambda callback: calls.append(callback) or True)
    ui._handle_global_input(KeyEvent("f4", event_type=KeyEventType.REPEAT))
    ui._handle_global_input(KeyEvent("t", ctrl=True, event_type=KeyEventType.REPEAT))
    assert calls == []
    assert not ui._details_open


def test_slash_helpers_are_local_and_do_not_dispatch_model_or_gateway(app, monkeypatch) -> None:
    kind, ui = app
    calls = []
    monkeypatch.setattr(ui, "_open_external_editor", lambda: calls.append(True))
    if kind == "coding":
        ui._handle_slash_command("keys", "")
        ui._handle_slash_command("editor", "")
        assert not ui._worker_active
        assert ui._command is None
    else:
        ui._handle_submit("/keys")
        ui._handle_submit("/editor")
        assert ui._send_queue.empty()
        assert ui._active_run_id is None
    assert calls == [True]
    assert "Active keybindings" in plain(ui._chat_log)


def test_custom_send_key_never_confirms_coding_permission(tmp_path: Path, monkeypatch) -> None:
    import xdog.tui.tui as module

    monkeypatch.setenv("CODING_DIR", str(tmp_path))
    (tmp_path / "keybindings.json").write_text(json.dumps({"send": "f2"}))
    session = MagicMock()
    session.agent.options.thinking = "off"
    session.working_dir = tmp_path
    ui = InteractiveMode(session)
    monkeypatch.setattr(module, "_terminal_width", lambda: 80)
    monkeypatch.setattr(module, "_terminal_height", lambda: 24)
    ui._tui._previous_width, ui._tui._previous_height = 80, 24
    try:
        ui._show_permission_request(PermissionRequest(
            id="permission", tool_name="bash", arguments={}, summary="Run a command",
        ))
        ui._layout.render(80)
        assert ui._permission_prompt is not None
        ui._tui._dispatch_input(KeyEvent("f2"))
        session.permissions.resolve.assert_not_called()
        ui._tui._dispatch_input(KeyEvent("g", ctrl=True))
        session.permissions.resolve.assert_not_called()
        ui._tui._dispatch_input(KeyEvent("escape"))
        session.permissions.resolve.assert_called_once_with("permission", "deny")
    finally:
        ui._request_exit()
