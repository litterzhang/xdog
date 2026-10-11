from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.tools import get_default_tools
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

FIXTURE = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions"


def create(tmp_path, mode="ask", project_only=False):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / "settings.json"
    marker = tmp_path / "marker"
    config = {"extensions": ["sample"], "extension_dir": str(root),
              "extension_settings": {"sample": {"marker": str(marker)}}}
    settings.write_text(json.dumps({} if project_only else config))
    if project_only:
        (tmp_path / ".coding").mkdir()
        (tmp_path / ".coding" / "settings.json").write_text(json.dumps(config))
    calls = []
    def stream(_model, context, _options):
        calls.append(context)
        last = context.messages[-1]
        text = str(last.content)
        if last.role == "user" and "external" in text:
            tool = next(tool for tool in context.tools if tool.name.startswith("ext_sample_"))
            message = AssistantMessage(content=(ToolCall(id="external", name=tool.name,
                                                       arguments={"value": "input"}),), stop_reason="toolUse")
        elif last.role == "user" and "rewrite" in text:
            message = AssistantMessage(content=(ToolCall(id="filesystem", name="filesystem",
                                                       arguments={"action": "read", "path": "rewrite"}),),
                                       stop_reason="toolUse")
        else:
            message = AssistantMessage(content=(TextContent(text="done"),))
        async def generate():
            yield DoneEvent(message=message, stop_reason=message.stop_reason)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)
    store = SessionManager(tmp_path / "sessions")
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model="test/model"),
                                       tools=get_default_tools(tmp_path), tool_ctx={"fs_workspace": str(tmp_path)}),
                           session_data=store.create_session(model="test/model", working_dir=tmp_path),
                           session_manager=store, settings=SettingsManager(global_path=settings, project_dir=tmp_path),
                           working_dir=tmp_path, tool_registry=None, bash=None, permissions=PermissionManager(mode))
    return session, calls, marker


@pytest.mark.parametrize("mode", ["ask", "allow-all"])
async def test_actual_session_extensions_tools_events_transforms_and_host_permissions(tmp_path, mode):
    session, calls, marker = create(tmp_path, mode)
    notices = []
    session.extensions.publish = notices.append
    try:
        assert not marker.exists()
        await session.send_message("external")
        assert len(calls) == 2 and marker.exists()
        result = calls[1].messages[-1]
        if mode == "allow-all":
            assert result.content[0].text == "EXTENSION-RESULT-input-POST"
        else:
            assert result.is_error and "approval" in result.content[0].text
        assert any("EXTENSION-TURN-START" in event.get("text", "") for event in notices)
        await session.send_message("rewrite")
        assert (tmp_path / "rewrite").exists() == (mode == "allow-all")
        # Permission checks use effective rewritten parameters, not the original read.
        session.plan_control("start", 0, "Inspect")
        (tmp_path / "rewrite").unlink(missing_ok=True)
        await session.send_message("rewrite")
        assert not (tmp_path / "rewrite").exists()
        await session.extensions.manage("unload", "sample")
        await session.initialize_extensions()
        assert not any(tool.name.startswith("ext_") for tool in session.agent.state.tools)
    finally:
        await session.extensions.aclose()
        session.dispose()
    assert marker.read_text().endswith("stopped\n")


async def test_project_config_cannot_activate_python_extensions(tmp_path):
    session, calls, marker = create(tmp_path, project_only=True)
    try:
        await session.send_message("normal")
        assert not marker.exists() and not session.extensions.enabled
        assert not any(tool.name.startswith("ext_") for tool in session.agent.state.tools)
    finally:
        await session.extensions.aclose()
        session.dispose()


def test_actual_ui_command_widget_notices_draft_and_session_ownership(tmp_path):
    session, calls, marker = create(tmp_path)
    ui = InteractiveMode(session)
    def wait(predicate):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            ui._poll()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("Extension command/UI did not settle")
    try:
        ui._editor.set_text("preserved draft")
        ui._handle_slash_command("extensions", "list")
        wait(lambda: ui._command is None)
        assert marker.exists() and not calls and ui._editor.get_text() == "preserved draft"
        assert "EXTENSION-WIDGET" in "\n".join(ui._extension_view.render(80))
        assert "ext-sample-hello" in ui._editor._command_provider()
        ui._handle_slash_command("ext-sample-hello", "user")
        wait(lambda: ui._command is None)
        assert "EXTENSION-COMMAND-STATUS" in "\n".join(ui._extension_view.render(80))
        assert not calls and ui._editor.get_text() == "preserved draft"
        old_id = session.session_id
        ui._handle_slash_command("new", "")
        wait(lambda: ui._command is None)
        ui._event_queue.put({"type": "extension_ui", "extension_session": old_id,
                             "snapshot": {"ui": {"stale": {"lines": ["OLD-WIDGET"]}}}})
        ui._poll()
        assert "OLD-WIDGET" not in "\n".join(ui._extension_view.render(80))
    finally:
        ui._request_exit()
        session.dispose()


def test_relative_extension_root_is_global_config_relative_not_project_relative(tmp_path):
    root = tmp_path / "user-config"
    root.mkdir()
    (root / "settings.json").write_text(json.dumps({"extension_dir": "trusted", "extensions": ["sample"]}))
    settings = SettingsManager(global_path=root / "settings.json", project_dir=tmp_path / "untrusted-project")
    assert settings.extension_root == root / "trusted"
