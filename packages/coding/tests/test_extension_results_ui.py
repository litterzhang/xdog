from __future__ import annotations

import asyncio
import base64
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, ImageContent, Model, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


@pytest.mark.parametrize("mode", ["ask", "allow-all"])
async def test_live_coding_rich_extension_image_structured_details_model_ui_and_persistence(tmp_path, mode):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"extensions": ["rich"], "extension_dir": str(root)}))
    contexts = []

    def stream(model, context, options):
        contexts.append(context)
        if len(contexts) == 1:
            tool = next(tool for tool in context.tools if tool.name.startswith("ext_rich_"))
            return EventStream.empty(
                AssistantMessage(content=(ToolCall(id="rich", name=tool.name, arguments={}),), stop_reason="toolUse")
            )
        return EventStream.empty(AssistantMessage(content=(TextContent(text="done"),)))

    catalog = SessionManager(tmp_path / "sessions")
    session = AgentSession(
        agent=Agent(stream, config=AgentConfig(model="test/model")),
        session_data=catalog.create_session(working_dir=tmp_path, model="test/model"),
        session_manager=catalog,
        settings=SettingsManager(global_path=settings),
        working_dir=tmp_path,
        tool_registry=None,
        bash=None,
        permissions=PermissionManager(mode),
    )
    ui = InteractiveMode(session)

    async def wait(predicate):
        async with asyncio.timeout(10):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)

    try:
        ui._handle_submit("run extension")
        if mode == "ask":
            await wait(lambda: ui._permission_prompt is not None)
            ui._permission_prompt.handle_input(KeyEvent("escape"))
        await wait(lambda: len(contexts) == 2 and not ui._worker_active)
        result = next(message for message in contexts[-1].messages if message.role == "toolResult")
        if mode == "ask":
            assert result.is_error and not any(isinstance(part, ImageContent) for part in result.content)
        else:
            images = [part for part in result.content if isinstance(part, ImageContent)]
            assert len(images) == 1 and base64.b64decode(images[0].data).startswith(b"\x89PNG")
            assert result.details["extension_details"] == {"count": 0, "enabled": False, "value": None}
            assert result.details["structured_content"] == [False, 0, None]
            assert not any(
                "POST-MUST-NOT-DROP-IMAGE" in part.text for part in result.content if isinstance(part, TextContent)
            )
            ui._poll()
            # Real result content is attached to the production tool component.
            assert any(getattr(child, "__class__").__name__ == "Image" for child in ui._chat_log._last_tool.children)
            saved = catalog.load_session(session.session_id)
            persisted = next(message for message in saved.messages if message.role == "toolResult")
            assert any(isinstance(part, ImageContent) for part in persisted.content)
            assert persisted.details["extension_details"] == result.details["extension_details"]
        assert session.permissions.mode == mode
    finally:
        ui._request_exit()
        await session.extensions.aclose()
        session.dispose()


async def test_coding_child_rich_extension_result_retains_images_and_independent_runtime(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"extensions": ["rich"], "extension_dir": str(root)}))
    contexts = []

    def stream(model, context, options):
        contexts.append(context)
        if context.messages[-1].role == "user":
            tool = next(tool for tool in context.tools if tool.name.startswith("ext_rich_"))
            return EventStream.empty(
                AssistantMessage(
                    content=(ToolCall(id="child-rich", name=tool.name, arguments={}),), stop_reason="toolUse"
                )
            )
        return EventStream.empty(AssistantMessage(content=(TextContent(text="child done"),)))

    # Child tool images require metadata from the actual configured adapter;
    # an opaque text-only fixture cannot establish image admission capability.
    stream.resolve_model = lambda name: Model(id=name, input=('text', 'image'), context_window=128000)
    catalog = SessionManager(tmp_path / "sessions")
    session = AgentSession(
        agent=Agent(stream, config=AgentConfig(model="test/model")),
        session_data=catalog.create_session(working_dir=tmp_path, model="test/model"),
        session_manager=catalog,
        settings=SettingsManager(global_path=settings),
        working_dir=tmp_path,
        tool_registry=None,
        bash=None,
        permissions=PermissionManager("allow-all"),
    )
    try:
        child = await session.children.supervisor.start("rich output", access="inherit")
        result = await session.children.supervisor.control("wait", child)
        assert result["status"] == "completed" and len(contexts) == 2
        tool_result = next(message for message in contexts[-1].messages if message.role == "toolResult")
        assert any(isinstance(part, ImageContent) for part in tool_result.content)
        assert tool_result.details["extension_details"]["enabled"] is False
        assert not session.agent.state.messages  # Child does not merge its conversation into the parent.
    finally:
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        session.dispose()
