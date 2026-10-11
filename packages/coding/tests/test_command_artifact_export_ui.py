from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

from xdog.agent import Agent, AgentConfig
from xdog.agent.extensions.command_result import read_artifact
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


def test_actual_coding_export_import_original_images_review_private_owner_and_no_model(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"extensions": ["rich"], "extension_dir": str(root)}))
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    store = SessionManager(tmp_path / "sessions")
    session = AgentSession(
        agent=Agent(stream, config=AgentConfig(model="test/model")),
        session_data=store.create_session(working_dir=tmp_path),
        session_manager=store,
        settings=SettingsManager(global_path=path),
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
        raise AssertionError("Artifact operation did not settle")

    try:
        ui._editor.set_text("parent draft")
        ui._handle_slash_command("ext-rich-show", "")
        wait(lambda: ui._command is None)
        original = next(item for item in ui._chat_log.children if item.__class__.__name__ == "ExtensionCommand")
        target = tmp_path / "saved.json"
        ui._output_command("export", f"{target} artifact")
        wait(lambda: ui._outputs.panel is not None)
        assert not target.exists() and ui._tui._focused is ui._outputs.panel
        panel = ui._outputs.panel
        panel.set_render_budget(12)
        wait(lambda: bool(panel.render(120)) and panel._visible)
        assert original.result["content"][1]["data"] not in panel.prepared.text
        panel.handle_input(KeyEvent("enter"))
        wait(lambda: not ui._outputs.pending and not ui._outputs.worker.busy)
        restored = read_artifact(target)
        assert restored.result == original.result and target.stat().st_mode & 0o777 == 0o600
        assert not session.messages and not calls and ui._editor.get_text() == "parent draft"
        ui._handle_slash_command("artifact", str(target))
        wait(
            lambda: len([item for item in ui._chat_log.children if item.__class__.__name__ == "ExtensionCommand"]) == 2
        )
        imported = [item for item in ui._chat_log.children if item.__class__.__name__ == "ExtensionCommand"][-1]
        assert imported.result == original.result and len(imported._images) == 1
        ui._output_command("export", f"{tmp_path / 'canceled.json'} artifact")
        wait(lambda: ui._outputs.panel is not None)
        ui._output_epoch += 1
        ui._poll()
        assert ui._outputs.panel is None and not (tmp_path / "canceled.json").exists()
        ui._output_command("export", f"{tmp_path / 'wrong-owner.json'} artifact")
        assert not ui._outputs.pending and not calls
        assert ui._editor.get_text() == "parent draft" and session.permissions.mode == "ask"
    finally:
        ui._request_exit()
        asyncio.run(session.extensions.aclose())
        session.dispose()


def test_stale_file_read_and_missing_owned_artifact_are_not_auto_exported(tmp_path):
    from unittest.mock import MagicMock

    from xdog.agent.extensions.command_result import ExtensionCommandResult
    from xdog.agent.extensions.tool_result import make_result
    session = MagicMock()
    session.working_dir = tmp_path
    session.session_id = 'current'
    ui = InteractiveMode(session)
    try:
        ui._editor.set_text('parent draft')
        artifact = ExtensionCommandResult('rich', 'ext-rich-show', make_result(structured=None))
        ui._handle_ui_event({'type': 'artifact_loaded', 'context': 'old-owner', 'artifact': artifact})
        assert not any(item.__class__.__name__ == 'ExtensionCommand' for item in ui._chat_log.children)
        ui._output_command('export', str(tmp_path / 'not-created.json') + ' artifact')
        assert not ui._outputs.pending and not (tmp_path / 'not-created.json').exists()
        assert ui._editor.get_text() == 'parent draft'
        session.agent.prompt.assert_not_called()
    finally:
        ui._request_exit()
