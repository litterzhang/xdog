"""Native clients retain explicit MCP operations without a manual completion route."""
from __future__ import annotations

import threading
from unittest.mock import AsyncMock, patch

import pytest
from xdog.agent.mcp.context import McpBlock, McpDocument, McpEntry
from xdog.claw.channels.tui import tui_app
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.modes.interactive import interactive_mode
from xdog.tui.keys import KeyEvent

from packages.coding.tests.test_mcp_browser import make_ui, wait_ui


@pytest.fixture(params=["coding", "claw"])
def native_mcp(request, tmp_path, monkeypatch):
    sent, calls = [], []
    session = None
    if request.param == "coding":
        ui, session, calls, _ = make_ui(tmp_path)
        ui._open_mcp()
        wait_ui(ui, lambda: not ui._mcp_panel._busy)
    else:
        ui = ChatApp("/unused-mcp-manual-picker-test.sock")
        monkeypatch.setattr(ui, "_queue_request", sent.append)
        ui._connection_ready = ui._mcp_supported = ui._mcp_data_supported = True
        ui._gateway_session_id = "owner"
        ui._open_mcp()
    panel = ui._mcp_panel
    ui._mcp_publish("list", ({"server": "fixture", "state": "connected"},))
    panel._server = "fixture"  # Catalog snapshots below belong to this selected server.
    try:
        yield request.param, ui, panel, sent, session, calls
    finally:
        ui._request_exit()
        if session is not None:
            session.dispose()


@pytest.mark.parametrize("action", ["complete_prompt", "complete_template", "complete_resource",
                                    "completion_cancel", "resource_completion_preview", "unknown-operation"])
def test_retired_picker_dispatch_and_reply_leave_active_native_read_untouched(native_mcp, action):
    host, ui, panel, sent, session, calls = native_mcp
    original = McpDocument("fixture", "resource", "uri", (McpBlock("text", "original"),))
    ui._mcp_document = original
    ui._mcp_active, ui._mcp_action = "in-flight-read", "read"
    panel._busy = True
    before = list(sent)
    if host == "coding":
        ui._mcp_cancel = threading.Event()
        sequence = ui._mcp_sequence
    ui._mcp_send(action, "fixture", "review", {"topic": "untrusted"})
    ui._mcp_publish(action, McpDocument("fixture", "resource", "stale", (McpBlock("text", "late"),)))
    assert ui._mcp_active == "in-flight-read" and ui._mcp_action == "read"
    assert panel._busy and panel._mode == "servers" and panel._preview is None
    assert ui._mcp_document is original and sent == before and not calls
    if host == "coding":
        assert not ui._mcp_cancel.is_set() and ui._mcp_sequence == sequence
        assert session.messages == []


def test_native_prompt_json_retrieval_preserves_parent_draft_without_model_input(native_mcp):
    host, ui, panel, sent, session, calls = native_mcp
    ui._editor.set_text("PARENT UNSENT")
    prompt = McpEntry("fixture", "prompt", "review", "Review", arguments=(("topic", "", True),))
    ui._mcp_publish("prompts", (prompt,))
    panel.handle_input(KeyEvent("enter"))
    panel._arguments.set_text('{"topic":"quoted \\\" literal 中文"}')
    for event in (KeyEvent("space", ctrl=True), KeyEvent("tab")):
        panel.handle_input(event)
    assert panel._mode == "arguments" and not hasattr(panel, "completion")
    if host == "coding":
        document = McpDocument("fixture", "prompt", "review", (McpBlock("text", "untrusted"),))
        with patch.object(session.mcp, "prompt", new_callable=AsyncMock, return_value=document) as retrieve:
            panel.handle_input(KeyEvent("enter"))
            wait_ui(ui, lambda: not panel._busy)
            retrieve.assert_awaited_once_with("fixture", "review", {"topic": 'quoted " literal 中文'})
        assert panel._mode == "preview" and session.messages == []
    else:
        panel.handle_input(KeyEvent("enter"))
        packet = sent[-1]
        assert packet["type"] == "mcp_context" and packet["action"] == "prompt"
        assert packet["name"] == "review" and packet["arguments"] == {"topic": 'quoted " literal 中文'}
    assert ui._editor.get_text() == "PARENT UNSENT" and not calls


def test_native_resolved_resource_read_remains_explicit_and_cancelable(native_mcp):
    host, ui, panel, sent, session, calls = native_mcp
    ui._editor.set_text("PARENT UNSENT")
    ui._mcp_publish("templates", (McpEntry("fixture", "template", "fixture://{name}", "Resource template"),))
    panel.handle_input(KeyEvent("enter"))
    panel.handle_input(KeyEvent("enter"))
    assert not panel._busy  # Unresolved expressions do not dispatch.
    panel._arguments.set_text("fixture://explicit/中文")
    if host == "coding":
        with patch.object(session.mcp, "resource", new_callable=AsyncMock,
                          side_effect=TimeoutError("MCP read timeout")) as retrieve:
            panel.handle_input(KeyEvent("enter"))
            wait_ui(ui, lambda: not panel._busy)
            retrieve.assert_awaited_once_with("fixture", "fixture://explicit/中文")
        assert "timeout" in panel._message and panel._arguments.get_text() == "fixture://explicit/中文"
        assert session.messages == []
    else:
        panel.handle_input(KeyEvent("enter"))
        packet = sent[-1]
        assert packet["type"] == "mcp_context" and packet["action"] == "read"
        assert packet["name"] == "fixture://explicit/中文" and packet["arguments"] == {}
        ui._close_mcp()
        assert {"type": "mcp_context_cancel", "request_id": packet["request_id"]} in sent
    assert ui._editor.get_text() == "PARENT UNSENT" and not calls


def test_picker_only_client_imports_are_absent():
    for module in (interactive_mode, tui_app):
        assert not hasattr(module, "McpCompletion")
        assert not hasattr(module, "resource_completion_document")
    assert not hasattr(interactive_mode, "request_arguments")


def test_old_completion_capabilities_do_not_restore_claw_picker_state():
    ui = ChatApp("/unused-mcp-capability-test.sock")
    try:
        ui._handle_response({"type": "pong", "mcp_context": 1, "mcp_completion": 1,
                             "mcp_resource_completion": 1})
        assert ui._mcp_data_supported
        assert not hasattr(ui, "_mcp_completion_supported")
        assert not hasattr(ui, "_mcp_resource_completion_supported")
    finally:
        ui._request_exit()
