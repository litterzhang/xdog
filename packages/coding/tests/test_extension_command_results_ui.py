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

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


def test_actual_coding_rich_command_artifact_inspectability_draft_and_no_agent_turn(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"extensions": ["rich"], "extension_dir": str(root)}))
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    store = SessionManager(tmp_path / "sessions")
    session = AgentSession(
        agent=Agent(stream, config=AgentConfig(model="test/model")),
        session_data=store.create_session(working_dir=tmp_path),
        session_manager=store,
        settings=SettingsManager(global_path=settings),
        working_dir=tmp_path,
        tool_registry=None,
        bash=None,
        permissions=PermissionManager("ask"),
    )
    ui = InteractiveMode(session)

    def wait(predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            ui._poll()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("Rich command did not settle")

    try:
        ui._editor.set_text("parent draft")
        ui._handle_slash_command("ext-rich-show", "")
        wait(lambda: ui._command is None)
        artifacts = [child for child in ui._chat_log.children if child.__class__.__name__ == "ExtensionCommand"]
        assert len(artifacts) == 1 and len(artifacts[0]._images) == 1
        assert artifacts[0].result["structuredContent"] == [False, 0, None]
        assert artifacts[0].result["details"]["enabled"] is False
        assert "[Extension command details]" in artifacts[0].detail_body
        ui._open_details()
        assert any(
            record.kind == "command" and "[false,0,null]" in record.body for record in ui._chat_log.detail_records()
        )
        ui._show_permission_request(PermissionRequest("approval", "bash", {}, "Host approval remains independent"))
        assert ui._tui._focused is ui._permission_prompt
        ui._permission_prompt._on_decision("deny")
        ui._close_details()
        assert ui._editor.get_text() == "parent draft" and ui._tui._focused is ui._editor
        assert not session.messages and not calls and session.permissions.mode == "ask"
        records = ui._chat_log.transcript_records()
        assert any(record.role == "system" and "[false,0,null]" in record.body for record in records)
    finally:
        ui._request_exit()
        asyncio.run(session.extensions.aclose())
        session.dispose()


def test_coding_stale_rich_command_cannot_append_or_change_the_new_owner(tmp_path):
    from unittest.mock import MagicMock

    from xdog.agent.extensions.command_result import ExtensionCommandResult
    from xdog.agent.extensions.tool_result import make_result
    session = MagicMock()
    session.working_dir = tmp_path
    session.session_id = 'new-session'
    ui = InteractiveMode(session)
    ui._update_footer = lambda: None
    ui._update_header = lambda: None
    try:
        ui._editor.set_text('new owner draft')
        artifact = ExtensionCommandResult('rich', 'ext-rich-show', make_result(structured=None))
        ui._handle_ui_event({'type': 'command_result', 'rich_output': artifact,
                             'owner_session': 'old-session', 'output': 'ignored', 'prompt': 'not a turn request'})
        assert not any(child.__class__.__name__ == 'ExtensionCommand' for child in ui._chat_log.children)
        assert ui._editor.get_text() == 'new owner draft'
        session.agent.prompt.assert_not_called()
    finally:
        ui._request_exit()
