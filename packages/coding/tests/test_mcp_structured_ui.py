from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/mcp_schema_peer.py"


@pytest.mark.parametrize("mode,choice", [("ask", "array"), ("allow-all", "array"), ("allow-all", "null"),
                                        ("ask", "error"), ("allow-all", "error"),
                                        ("ask", "content"), ("allow-all", "content")])
async def test_live_coding_nullable_tool_structured_data_permission_and_full_source(tmp_path, mode, choice):
    path = tmp_path / "settings.json"
    log = tmp_path / "requests"
    path.write_text(
        json.dumps(
            {"mcp_servers": {"peer": {"command": sys.executable, "args": [str(FIXTURE), "modern-" + choice, str(log)]}}}
        )
    )
    catalog = SessionManager(tmp_path / "sessions")
    contexts, name = [], []

    def stream(model, context, options):
        contexts.append(context)
        if len(contexts) == 1:
            return EventStream.empty(
                AssistantMessage(
                    content=(ToolCall(id="rich", name=name[0], arguments={"value": None}),), stop_reason="toolUse"
                )
            )
        return EventStream.empty(AssistantMessage(content=(TextContent(text="done"),)))

    session = AgentSession(
        agent=Agent(stream, config=AgentConfig(model="test/model")),
        session_data=catalog.create_session(model="test/model", working_dir=tmp_path),
        session_manager=catalog,
        settings=SettingsManager(global_path=path),
        working_dir=tmp_path,
        tool_registry=None,
        bash=None,
        permissions=PermissionManager(mode),
    )
    ui = InteractiveMode(session)

    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)

    task = None
    try:
        await session.manage_mcp("connect", "peer")
        name.append(session.mcp.tools()[0].name)
        ui._handle_submit("run rich tool")
        if mode == "ask":
            await wait(lambda: ui._permission_prompt is not None)
            assert not any(json.loads(line)["method"] == "tools/call" for line in log.read_text().splitlines())
            ui._permission_prompt.handle_input(KeyEvent("escape"))
        await wait(lambda: len(contexts) == 2 and not ui._worker_active)
        ui._poll()
        assert len(contexts) == 2
        calls = [
            json.loads(line) for line in log.read_text().splitlines() if json.loads(line)["method"] == "tools/call"
        ]
        if mode == "ask":
            assert not calls and any(
                message.role == "toolResult" and message.is_error for message in contexts[-1].messages
            )
        else:
            assert len(calls) == 1
            result = next(message for message in contexts[-1].messages if message.role == "toolResult")
            expected = ([{"tag": "STRUCTURED-ARRAY-TAIL-987"}] if choice in ("array", "content") else
                        {"diagnostic": "ERROR-STRUCTURED-TAIL", "retry": False} if choice == "error" else None)
            assert result.details["structured_content"] == expected and result.details["structured_content_present"]
            body = "\n".join(record.body for record in ui._chat_log.transcript_records() if record.role == "tool")
            assert "[MCP structured output]" in body
            assert ("STRUCTURED-ARRAY-TAIL-987" if choice in ("array", "content") else
                    "ERROR-STRUCTURED-TAIL" if choice == "error" else "null") in body
            persisted = catalog.load_session(session.session_data.session_id)
            assert persisted is not None
            saved = next(message for message in persisted.messages if message.role == "toolResult")
            assert saved.details["structured_content"] == expected and saved.details["structured_content_present"]
            if choice == "content":
                assert 'MCP-CONTENT-METADATA' in body and 'MCP-IMAGE-METADATA' in body
                assert 'MCP-CONTENT-AUDIO' in body and 'MCP-CONTENT-LINK' in body
                assert result.details['mcp_content_metadata'][0]['metadata']['annotations']['priority'] == 0.25
                assert saved.details['mcp_content_metadata'] == result.details['mcp_content_metadata']
            if choice == "error":
                from xdog.ai.types import ImageContent
                assert result.is_error and saved.is_error
                assert any(isinstance(part, ImageContent) for part in result.content)
                assert any(isinstance(part, ImageContent) for part in saved.content)
                assert "RICH-MCP-ERROR" in body
        assert session.permissions.mode == mode
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        ui._request_exit()
        await session.mcp.aclose()
        session.dispose()
