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
from xdog.coding.core.permissions import PermissionManager, PermissionRequest
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.prompt_keymap import PromptKeymap

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


@pytest.mark.parametrize("remap", [False, True])
def test_live_coding_shortcut_uses_real_command_editor_trust_conflicts_and_context_priority(tmp_path, remap):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"extensions": ["keyed"], "extension_dir": str(root)}))
    store = SessionManager(tmp_path / "sessions")
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

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
        raise AssertionError("Shortcut command did not settle")

    try:
        ui._editor.set_text("parent draft")
        assert not ui._extension_shortcut_input(KeyEvent("k", alt=True))  # Does not load plugins from a key.
        ui._handle_slash_command("extensions", "")
        wait(lambda: ui._command is None)
        assert ui._extension_snapshot.get("shortcuts")
        if remap:
            ui._keymap = PromptKeymap.from_overrides({"ext-keyed-compose": "alt+x"})
            ui._editor.set_keymap(ui._keymap)
        event = KeyEvent("x" if remap else "k", alt=True)
        assert not ui._extension_shortcut_input(KeyEvent(event.key, alt=True, event_type=KeyEventType.REPEAT))
        ui._show_permission_request(PermissionRequest("approval", "bash", {}, "Independent approval"))
        assert not ui._extension_shortcut_input(event)
        ui._permission_prompt._on_decision("deny")
        ui._open_details()
        assert not ui._extension_shortcut_input(event)
        ui._close_details()
        assert ui._handle_global_input(event) == {"consume": True}
        assert not ui._extension_shortcut_input(event)  # Active command prevents callback duplicates.
        wait(lambda: ui._command is None)
        assert ui._editor.get_text() == "parent draft SHORTCUT-DRAFT" and not calls and not session.messages
        assert session.permissions.mode == "ask"
        ui._show_keybindings()
        body = "\n".join(ui._chat_log.render(120))
        assert "ext-keyed-compose" in body and "DISABLED" in body and "conflicts with finish" in body
        ui._handle_slash_command("extensions", "unload keyed")
        wait(lambda: ui._command is None)
        assert not ui._extension_shortcut_input(event)
        assert ui._editor.get_text() == "parent draft SHORTCUT-DRAFT"
    finally:
        ui._request_exit()
        asyncio.run(session.extensions.aclose())
        session.dispose()
