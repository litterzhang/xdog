from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from xdog.agent import Agent, AgentConfig
from xdog.agent.skills import SkillRepo
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.tools import get_default_tools

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
        if last.role == "user" and "rewrite" in text:
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
    session = AgentSession(agent=Agent(stream,
                                       skills=SkillRepo().register(directory=tmp_path / 'skills', name="shared"),
                                       config=AgentConfig(model="test/model"),
                                       tools=get_default_tools(tmp_path), tool_ctx={"fs_workspace": str(tmp_path)}),
                           session_data=store.create_session(model="test/model", working_dir=tmp_path),
                           session_manager=store, settings=SettingsManager(global_path=settings, project_dir=tmp_path),
                           working_dir=tmp_path, tool_repo=None, bash=None, permissions=PermissionManager(mode))
    return session, calls, marker


async def test_project_config_cannot_activate_python_extensions(tmp_path):
    session, calls, marker = create(tmp_path, project_only=True)
    try:
        await session.send_message("normal")
        assert not marker.exists() and not session.extensions.enabled
        # The built-in host query is not Python code activated by project settings.
        # No project-selected worker tool may appear alongside that exact binding.
        prefixed = [tool for tool in session.agent.state.tools if tool.name.startswith("ext_")]
        assert len(prefixed) == 1 and prefixed[0] is session._session_info_tool
        assert prefixed[0].name == 'ext_coding_session'
    finally:
        await session.extensions.aclose()
        session.dispose()


def test_relative_extension_root_is_global_config_relative_not_project_relative(tmp_path):
    root = tmp_path / "user-config"
    root.mkdir()
    (root / "settings.json").write_text(json.dumps({"extension_dir": "trusted", "extensions": ["sample"]}))
    settings = SettingsManager(global_path=root / "settings.json", project_dir=tmp_path / "untrusted-project")
    assert settings.extension_root == root / "trusted"
