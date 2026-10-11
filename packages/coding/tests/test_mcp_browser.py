from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent

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


def test_actual_coding_browser_connect_read_preview_and_insert_keeps_data_unsent(tmp_path):
    ui, session, provider_calls, log = make_ui(tmp_path)
    def wait(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            ui._poll()
            if predicate():
                return
            time.sleep(0.002)
        raise AssertionError("MCP browser did not settle")
    try:
        ui._editor.set_text("preserved draft")
        ui._open_mcp()
        panel = ui._mcp_panel
        wait(lambda: not panel._busy)
        assert not log.exists()
        panel.handle_input(KeyEvent("c"))
        wait(lambda: not panel._busy and ui._command is None)
        assert session.mcp.status()[0]["state"] == "connected"
        panel.handle_input(KeyEvent("u"))
        wait(lambda: not panel._busy)
        assert panel._mode == "catalog"
        panel.handle_input(KeyEvent("enter"))
        wait(lambda: not panel._busy)
        assert ui._mcp_document is not None and len(ui._mcp_document.text) > 200000
        assert ui._editor.get_text() == "preserved draft" and not provider_calls
        panel.handle_input(KeyEvent("i"))
        assert ui._mcp_panel is None and ui._tui._focused is ui._editor
        assert "RESOURCE-TAIL" in ui._editor.get_text() and "preserved draft" in ui._editor.get_text()
        assert "User-selected untrusted MCP resource" in ui._editor.get_text()
        assert not provider_calls
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        assert not any(frame["method"] == "tools/call" for frame in frames)
    finally:
        ui._request_exit()
        session.dispose()


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


def test_coding_explicit_prompt_template_completion_preserves_draft_and_never_runs_tools(tmp_path):
    ui, session, calls, log = make_ui(tmp_path)
    try:
        ui._editor.set_text('parent draft')
        ui._open_mcp()
        panel = ui._mcp_panel
        wait_ui(ui, lambda: not panel._busy)
        panel.handle_input(KeyEvent('c'))
        wait_ui(ui, lambda: not panel._busy and ui._command is None)
        panel.handle_input(KeyEvent('p'))
        wait_ui(ui, lambda: not panel._busy)
        panel.handle_input(KeyEvent('enter'))
        panel.handle_input(KeyEvent('space', ctrl=True))
        assert panel.completion is not None
        panel.handle_input(KeyEvent('tab'))
        wait_ui(ui, lambda: not panel._busy and panel.completion._receipt is None)
        assert panel.completion._values == ('completed value', 'alternative')
        from xdog.tui.components.text import Text
        protected = ui._tui.show_overlay(Text('Protected host surface'))
        assert not panel.completion.focused
        panel.render(80)
        panel.handle_paste('wrong editor')
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'completion' and panel._arguments.get_text() == '{}'
        assert panel.completion._input.get_text() == '' and not panel.completion._visible
        protected.hide()
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'completion'  # Focus restoration alone is not insertion proof.
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        assert json.loads(panel._arguments.get_text()) == {'topic': 'completed value'}
        assert not calls and ui._editor.get_text() == 'parent draft' and ui._mcp_document is None
        panel.handle_input(KeyEvent('enter'))
        wait_ui(ui, lambda: panel._mode == 'preview' and not panel._busy)
        assert 'PROMPT-completed value' in ui._mcp_document.text
        panel.handle_input(KeyEvent('escape'))
        wait_ui(ui, lambda: not panel._busy)
        panel.handle_input(KeyEvent('t'))
        wait_ui(ui, lambda: not panel._busy)
        panel.handle_input(KeyEvent('enter'))
        panel.handle_input(KeyEvent('space', ctrl=True))
        panel.handle_input(KeyEvent('tab'))
        wait_ui(ui, lambda: not panel._busy)
        assert panel.completion._values
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        assert panel._arguments.get_text() == 'fixture://document/completed%20value'
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        assert sum(frame['method'] == 'completion/complete' for frame in frames) == 2
        assert not any(frame['method'] in ('tools/call', 'resources/read') for frame in frames)
        assert ui._editor.get_text() == 'parent draft' and not calls
        ui._close_mcp()
        assert ui._tui._focused is ui._editor
    finally:
        ui._request_exit()
        session.dispose()


@pytest.mark.parametrize('completion_only', [False, True])
def test_coding_opaque_completion_reviews_data_without_resource_read_or_model_turn(tmp_path, completion_only):
    peer = FIXTURE.with_name('mcp_completion_peer.py') if completion_only else FIXTURE
    expected = 'completed value' if completion_only else 'OPAQUE-ARGUMENT-VALUE'
    ui, session, calls, log = make_ui(tmp_path, peer=peer, mode='completion-only' if completion_only else 'modern')
    try:
        ui._editor.set_text('parent draft')
        ui._open_mcp()
        panel = ui._mcp_panel
        wait_ui(ui, lambda: not panel._busy)
        panel.handle_input(KeyEvent('c'))
        wait_ui(ui, lambda: not panel._busy and ui._command is None)
        if not completion_only:
            panel.handle_input(KeyEvent('u'))
            wait_ui(ui, lambda: not panel._busy)
        panel.handle_input(KeyEvent('v'))
        assert panel._mode == 'resource_arguments'
        panel._arguments.set_text(json.dumps({'uri': 'fixture://text', 'argument': 'opaque-field',
                                              'value': 'pre', 'context': {'language': '中文'}}))
        panel.handle_input(KeyEvent('space', ctrl=True))
        panel.handle_input(KeyEvent('tab'))
        wait_ui(ui, lambda: not panel._busy and panel.completion._receipt is None)
        assert panel.completion._values[0] == expected
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        assert ui._mcp_document is None and ui._editor.get_text() == 'parent draft' and not calls
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'preview' and ui._mcp_document.kind == 'resource-completion'
        assert 'NOT a resource read or resolved URI' in ui._mcp_document.text
        assert expected in ui._mcp_document.text
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        assert not any(frame['method'] in ('resources/read', 'prompts/get', 'tools/call') for frame in frames)
        assert len([frame for frame in frames if frame['method'] == 'completion/complete']) == 1
        assert ui._editor.get_text() == 'parent draft' and not calls
        panel.handle_input(KeyEvent('i'))
        assert ui._mcp_panel is None and ui._tui._focused is ui._editor
        assert 'parent draft' in ui._editor.get_text() and expected in ui._editor.get_text()
        assert 'untrusted MCP resource-completion' in ui._editor.get_text() and not calls
    finally:
        ui._request_exit()
        session.dispose()


def test_actual_empty_binary_preview_keeps_type_blocks_draft_and_exports_zero_bytes_losslessly(tmp_path):
    ui, session, calls, log = make_ui(tmp_path, mode='empty-content')
    try:
        ui._editor.set_text('kept draft')
        ui._open_mcp()
        panel = ui._mcp_panel
        wait_ui(ui, lambda: not panel._busy)
        panel.handle_input(KeyEvent('c'))
        wait_ui(ui, lambda: not panel._busy and ui._command is None)
        panel.handle_input(KeyEvent('u'))
        wait_ui(ui, lambda: not panel._busy)
        panel.handle_input(KeyEvent('enter'))
        wait_ui(ui, lambda: not panel._busy)
        assert ui._mcp_document.blocks[0].content_type == 'resource_blob'
        assert '0 bytes' in ui._mcp_document.text and 'EMPTY-BINARY-METADATA' in ui._mcp_document.text
        panel.handle_input(KeyEvent('i'))
        assert ui._mcp_panel is panel and ui._editor.get_text() == 'kept draft'
        panel.handle_input(KeyEvent('e'))
        wait_ui(ui, lambda: ui._outputs.panel is not None)
        exported = json.loads(ui._outputs._prepared.data)['records'][0]['body']
        block = json.loads(exported)['blocks'][0]
        assert block['content_type'] == 'resource_blob' and block['data'] == ''
        ui._outputs.cancel()
        ui._poll()
        ui._close_mcp()
        assert ui._editor.get_text() == 'kept draft' and not calls and ui._tui._focused is ui._editor
        requests = [json.loads(line)['method'] for line in log.read_text().splitlines()]
        assert requests.count('resources/read') == 1 and 'tools/call' not in requests
    finally:
        ui._request_exit()
        session.dispose()
