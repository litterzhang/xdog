from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

FIXTURE = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "mcp_server.py"


def make_ui(tmp_path, *, peer=FIXTURE, mode="modern"):
    settings = tmp_path / "settings.json"
    log = tmp_path / "requests"
    settings.write_text(json.dumps({"mcp_servers": {"fixture": {"command": sys.executable,
                                     "args": [str(peer), mode, str(log)]}}}))
    manager = SessionManager(tmp_path / "sessions")
    provider_calls = []
    def stream(*args):
        provider_calls.append(args)
        return EventStream.empty(AssistantMessage())
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model="test/model")),
                           session_data=manager.create_session(model="test/model", working_dir=tmp_path),
                           session_manager=manager, settings=SettingsManager(global_path=settings),
                           working_dir=tmp_path, tool_registry=None, bash=None)
    ui = InteractiveMode(session)
    return ui, session, provider_calls, log


def wait_ui(ui, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        ui._poll()
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("UI did not settle")


def test_browser_focus_restores_after_approval_output_cancel_and_replacement(tmp_path):
    from xdog.agent.mcp.context import McpBlock, McpDocument
    from xdog.coding.core.permissions import PermissionRequest
    ui, session, calls, _log = make_ui(tmp_path)
    try:
        ui._open_mcp()
        panel = ui._mcp_panel
        wait_ui(ui, lambda: not panel._busy)
        assert "MCP focused" in ui._hints._text
        ui._show_permission_request(PermissionRequest("approve", "bash", {"command": "echo ok"}, "Run echo"))
        assert "Permission focused" in ui._hints._text
        approval = ui._permission_prompt
        ui._restore_auxiliary_focus()
        assert ui._tui._focused is approval
        ui._open_transcript()  # Never replace a live approval with another panel.
        assert ui._mcp_panel is panel and ui._transcript_panel is None
        approval._on_decision("deny")
        assert ui._tui._focused is panel
        ui._mcp_document = McpDocument("fixture", "resource", "uri", (McpBlock("resource", data="AAECAw=="),))
        ui._mcp_insert()
        assert ui._mcp_panel is panel and ui._editor.get_text() == "" and not calls
        ui._mcp_export()
        wait_ui(ui, lambda: ui._outputs.panel is not None)
        assert "Output review" in ui._hints._text
        assert ui._layout.details is ui._outputs.panel
        assert ui._tui._focused is ui._outputs.panel
        assert "AAECAw==" in ui._outputs._prepared.data.decode()
        assert ui._outputs._root == tmp_path
        assert not list(tmp_path.glob("transcript-*.json"))  # Review is not a write.
        ui._outputs.cancel()
        ui._poll()
        assert ui._layout.details is panel and ui._tui._focused is panel
        assert "MCP focused" in ui._hints._text
        ui._open_transcript()
        assert ui._mcp_panel is None and ui._tui._focused is ui._transcript_panel
    finally:
        ui._request_exit()
        session.dispose()


def test_browser_close_cancels_read_and_ignores_late_preview(tmp_path):
    from xdog.agent.mcp.context import McpBlock, McpDocument
    ui, session, calls, _log = make_ui(tmp_path)
    entered, canceled = threading.Event(), threading.Event()
    async def slow(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()
    try:
        ui._open_mcp()
        wait_ui(ui, lambda: not ui._mcp_panel._busy)
        with patch.object(session.mcp, "resource", slow):
            ui._mcp_send("read", "fixture", "uri", {})
            identifier = ui._mcp_active
            wait_ui(ui, entered.is_set)
            ui._close_mcp()
            wait_ui(ui, canceled.is_set)
            ui._mcp_thread.join(2)
            assert not ui._mcp_thread.is_alive()
        ui._open_mcp()
        panel = ui._mcp_panel
        wait_ui(ui, lambda: not panel._busy)
        ui._event_queue.put({"type": "mcp_view_result", "id": identifier, "action": "read",
                             "data": McpDocument("fixture", "resource", "uri", (McpBlock("resource", "late"),))})
        ui._poll()
        assert panel._mode == "servers" and ui._mcp_document is None and not calls
        ui._output_epoch += 1
        ui._poll()
        assert ui._mcp_panel is None and ui._tui._focused is ui._editor
    finally:
        ui._request_exit()
        session.dispose()
