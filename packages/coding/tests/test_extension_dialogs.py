from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager, PermissionRequest
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent

FIXTURE = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions"


def test_live_coding_command_dialog_sequence_draft_approval_focus_and_no_auto_turn(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"extensions": ["dialogue"], "extension_dir": str(root)}))
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    store = SessionManager(tmp_path / "sessions")
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model="test/model")),
                           session_data=store.create_session(working_dir=tmp_path), session_manager=store,
                           settings=SettingsManager(global_path=settings), working_dir=tmp_path,
                           tool_registry=None, bash=None, permissions=PermissionManager("ask"))
    ui = InteractiveMode(session)
    def wait(predicate):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            ui._poll()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("Dialog UI did not settle")
    try:
        ui._editor.set_text("preserved draft")
        ui._handle_slash_command("ext-dialogue-ask", "")
        wait(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        assert panel.dialog["kind"] == "select" and ui._command is not None
        session_id = session.session_id
        ui._show_permission_request(PermissionRequest("req", "bash", {}, "Approve?"))
        ui._sync_extension_dialog()
        assert ui._tui._focused is ui._permission_prompt
        ui._permission_prompt._on_decision("deny")
        assert ui._tui._focused is panel
        panel.handle_input(KeyEvent("down"))
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        wait(lambda: ui._extension_dialog_panel is not None and ui._extension_dialog_panel.dialog["kind"] == "input")
        panel = ui._extension_dialog_panel
        panel._input.set_text("user")
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        wait(lambda: ui._extension_dialog_panel is not None and ui._extension_dialog_panel.dialog["kind"] == "confirm")
        panel = ui._extension_dialog_panel
        panel.handle_input(KeyEvent("down"))
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        wait(lambda: ui._command is None)
        assert ui._extension_dialog_panel is None and ui._tui._focused is ui._editor
        assert not calls and ui._editor.get_text() == "preserved draft" and session.permissions.mode == "ask"
        ui._event_queue.put({"type": "extension_dialog", "extension_session": "stale", "extension": "dialogue",
                             "dialog": {"id": "old", "kind": "confirm", "title": "old"}})
        ui._poll()
        assert ui._extension_dialog_panel is None and session.session_id == session_id
    finally:
        ui._request_exit()
        asyncio.run(session.extensions.aclose())
        session.dispose()


def test_live_coding_typed_form_whole_review_preserves_draft_and_permission_priority(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['dialogue'], 'extension_dir': str(root)}))
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    store = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
        permissions=PermissionManager('ask'))
    ui = InteractiveMode(session)
    def wait(predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            ui._poll()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError('Form did not settle')
    try:
        ui._editor.set_text('parent draft')
        ui._handle_slash_command('ext-dialogue-form', '')
        wait(lambda: ui._extension_dialog_panel is not None)
        wrapper = ui._extension_dialog_panel
        assert wrapper.dialog['kind'] == 'form'
        ui._show_permission_request(PermissionRequest('approval', 'bash', {}, 'Host approval'))
        ui._sync_extension_dialog()
        assert ui._tui._focused is ui._permission_prompt
        ui._permission_prompt._on_decision('deny')
        assert ui._tui._focused is wrapper
        wrapper.handle_paste('Alice')
        wrapper.handle_input(KeyEvent('enter'))
        assert wrapper._form._phase == 'review' and ui._command is not None
        wrapper.handle_input(KeyEvent('enter'))
        assert ui._command is not None  # Unpainted whole-form review cannot submit.
        for _ in range(60):
            wrapper.render(80)
            if wrapper._form._visible:
                break
            wrapper.handle_input(KeyEvent('pagedown'))
        assert wrapper._form._visible
        wrapper.handle_input(KeyEvent('enter'))
        wait(lambda: ui._command is None)
        assert ui._extension_dialog_panel is None and ui._editor.get_text() == 'parent draft'
        assert ui._tui._focused is ui._editor and not calls and session.permissions.mode == 'ask'
        assert '"enabled": false' in '\n'.join(ui._chat_log.render(100))
    finally:
        ui._request_exit()
        asyncio.run(session.extensions.aclose())
        session.dispose()
