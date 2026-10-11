from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager

FIXTURE = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "mcp_server.py"


@pytest.mark.parametrize("mode", ["ask", "allow-all"])
async def test_real_session_external_tools_use_the_one_agent_loop_and_existing_permission_gate(tmp_path, mode):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"mcp_servers": {"fixture": {
        "command": sys.executable, "args": [str(FIXTURE), "modern", str(tmp_path / "requests")],
    }}}))
    catalog = SessionManager(tmp_path / "sessions")
    calls = []
    selected = []
    def stream_fn(model, context, options):
        calls.append(context)
        call = ToolCall(id="call", name=selected[0], arguments={"value": "remote result"})
        message = (AssistantMessage(content=(call,),
                                    stop_reason="toolUse") if len(calls) == 1
                   else AssistantMessage(content=(TextContent(text="done"),)))
        async def generate():
            yield DoneEvent(message=message, stop_reason=message.stop_reason)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)
    agent = Agent(stream_fn, config=AgentConfig(model="test/model"))
    session = AgentSession(agent=agent, session_data=catalog.create_session(model="test/model", working_dir=tmp_path),
                           session_manager=catalog, settings=SettingsManager(global_path=settings_path),
                           working_dir=tmp_path, tool_registry=None, bash=None, permissions=PermissionManager(mode))
    try:
        assert not (tmp_path / "requests").exists()
        await session.manage_mcp("connect", "fixture")
        selected.append(next(tool.name for tool in agent.state.tools if tool.name.startswith("mcp_fixture_")))
        before = (tmp_path / "requests").read_text()
        response = await session.send_message("call external tool")
        assert response is not None and len(calls) == 2
        frames = [json.loads(line) for line in (tmp_path / "requests").read_text().splitlines()]
        if mode == "ask":
            # Even a server claiming readOnlyHint cannot authorize unattended execution.
            assert not any(frame["method"] == "tools/call" for frame in frames)
            assert (tmp_path / "requests").read_text() == before
        else:
            assert sum(frame["method"] == "tools/call" for frame in frames) == 1
            assert any(getattr(message, "role", "") == "toolResult" for message in calls[1].messages)
        await session.manage_mcp("disconnect", "fixture")
        assert not any(tool.name.startswith("mcp_fixture_") for tool in agent.state.tools)
    finally:
        await session.mcp.aclose()
        session.dispose()


async def test_workspace_switch_drops_external_proxies_and_reloads_project_server_config(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}")
    (tmp_path / ".coding").mkdir()
    (tmp_path / ".coding" / "settings.json").write_text(json.dumps({"mcp_servers": {"fixture": {
        "command": sys.executable, "args": [str(FIXTURE), "modern", str(tmp_path / "requests")],
    }}}))
    other = tmp_path / "other"
    other.mkdir()
    catalog = SessionManager(tmp_path / "sessions")
    first = catalog.create_session(model="test/model", working_dir=tmp_path)
    target = catalog.create_session(model="test/model", working_dir=other)
    catalog.save_session(target)
    agent = Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/model"))
    session = AgentSession(agent=agent, session_data=first, session_manager=catalog,
                           settings=SettingsManager(global_path=settings_path, project_dir=tmp_path),
                           working_dir=tmp_path, tool_registry=None, bash=None)
    try:
        await session.manage_mcp("connect", "fixture")
        old = session.mcp
        assert agent.state.tools
        assert session.switch_session(target.session_id)
        assert not any(tool.name.startswith("mcp_") for tool in agent.state.tools)
        assert session.mcp is not old and not session.mcp.configs
    finally:
        await session.mcp.aclose()
        session.dispose()
