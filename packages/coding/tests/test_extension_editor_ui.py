from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.attachments import attachment
from xdog.tui.keys import KeyEvent

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


@pytest.mark.parametrize("change", [False, True])
def test_actual_coding_command_editor_cas_draft_attachments_and_no_model_turn(tmp_path, change):
    import base64

    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"extensions": ["editor"], "extension_dir": str(root)}))
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    catalog = SessionManager(tmp_path / "sessions")
    session = AgentSession(
        agent=Agent(stream, config=AgentConfig(model="test/model")),
        session_data=catalog.create_session(working_dir=tmp_path),
        session_manager=catalog,
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
        raise AssertionError("Editor command did not settle")

    try:
        ui._editor.set_text("parent draft")
        image = attachment(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII="
            ),
            "retained.png",
        )
        ui._composer.items[image.id] = image
        ui._handle_slash_command("ext-editor-compose", "pause")
        wait(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        assert panel.dialog["kind"] == "confirm"
        if change:
            ui._editor.set_text("user changed draft")
        panel.handle_input(KeyEvent("down"))
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        wait(lambda: ui._command is None)
        expected = "user changed draft" if change else "parent draft EXTENSION-COMPOSED"
        assert ui._editor.get_text() == expected and ui._composer.items[image.id] is image
        assert not calls and not session.messages and session.permissions.mode == "ask"
        assert ui._tui._focused is ui._editor
    finally:
        ui._request_exit()
        asyncio.run(session.extensions.aclose())
        session.dispose()


def test_command_editor_cannot_sanitize_or_submit_terminal_control_text(tmp_path):
    from unittest.mock import MagicMock
    session = MagicMock()
    session.working_dir = tmp_path
    ui = InteractiveMode(session)
    try:
        ui._editor.set_text('preserved')
        assert not ui._command_editor_replace('\x1b[2Jmutation')
        assert ui._editor.get_text() == 'preserved'
        session.agent.prompt.assert_not_called()
    finally:
        ui._request_exit()
