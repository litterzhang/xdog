from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager

FIXTURE = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions"


def create(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"extensions": ["contextual"], "extension_dir": str(root)}))
    contexts = []
    def stream(model, context, options):
        contexts.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="done"),)))
    store = SessionManager(tmp_path / "sessions")
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model="test/model")),
                           session_data=store.create_session(working_dir=tmp_path), session_manager=store,
                           settings=SettingsManager(global_path=settings), working_dir=tmp_path,
                           tool_registry=None, bash=None, permissions=PermissionManager("ask"))
    return session, contexts


async def test_actual_coding_context_is_ephemeral_and_entries_follow_session_branch_and_reload(tmp_path):
    session, contexts = create(tmp_path)
    try:
        await session.extension_command("ext-contextual-remember", "first")
        await session.send_message("request")
        assert "EXTENSION-CONTEXT-first" in str(contexts[-1].messages[-1].content)
        assert not any("EXTENSION-CONTEXT" in str(getattr(message, "content", "")) for message in session.messages)
        branch = session.create_branch()
        await session.extension_command("ext-contextual-remember", "second")
        assert len(session.extensions.entries.for_extension("contextual")) == 2
        old = session.extensions
        assert session.restore_branch(branch)
        await session.send_message("branch request")
        assert "EXTENSION-CONTEXT-first" in str(contexts[-1].messages[-1].content)
        assert len(session.extensions.entries.for_extension("contextual")) == 1
        loaded = session.session_manager.load_session(session.session_id)
        assert loaded.extension_entries["contextual"][0]["data"] == {"text": "first"}
        with pytest.raises(RuntimeError, match="closed"):
            old.entries.append("contextual", [{"kind": "note", "data": "late"}])
        saved_id = session.session_id
        session.new_session()
        await session.send_message("fresh")
        assert not session.extensions.entries.snapshot()
        assert session.switch_session(saved_id)
        await session.send_message("resume")
        assert "EXTENSION-CONTEXT-first" in str(contexts[-1].messages[-1].content)
        assert session.permissions.mode == "ask"
    finally:
        await session.extensions.aclose()
        session.dispose()


async def test_existing_sdk_transform_is_composed_without_changing_host_system_prompt(tmp_path):
    session, contexts = create(tmp_path)
    async def base(messages):
        from xdog.ai.types import UserMessage
        return [*messages, UserMessage(content="BASE-CONTEXT")]
    session._base_context_transform = base
    try:
        await session.extension_command("ext-contextual-remember", "entry")
        session.plan_control("start", 0, "Inspect only")
        await session.send_message("request")
        assert any("BASE-CONTEXT" in str(message.content) for message in contexts[-1].messages)
        assert "EXTENSION-CONTEXT-entry" in str(contexts[-1].messages[-1].content)
        assert "Planning is read-only" in str(contexts[-1].system_prompt)
        assert session.plan.state.mode == "planning"
        assert len(session.messages) == 2
    finally:
        await session.extensions.aclose()
        session.dispose()
