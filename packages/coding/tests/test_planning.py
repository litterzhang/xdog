from __future__ import annotations

import asyncio
import time

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.slash_commands import execute_command
from xdog.coding.core.tools import get_default_tools
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent


def create(tmp_path, mode="ask"):
    turns = []
    def stream(_model, context, _options):
        turns.append(context)
        last = context.messages[-1]
        if last.role == "user" and "build" in str(last.content):
            call = ToolCall(id="plan", name="update_plan", arguments={"expected_revision": 1,
                           "steps": [{"description": "Inspect", "status": "completed"},
                                     {"description": "Implement", "status": "pending"}]})
            message = AssistantMessage(content=(call,), stop_reason="toolUse")
        elif last.role == "user" and "write" in str(last.content):
            message = AssistantMessage(content=(ToolCall(id="write", name="filesystem", arguments={
                "action": "write", "path": "target", "content": "changed"}),), stop_reason="toolUse")
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
                           session_manager=store, settings=SettingsManager(global_path=tmp_path / "settings.json"),
                           tool_registry=None, bash=None, working_dir=tmp_path, permissions=PermissionManager(mode))
    return session, turns


@pytest.mark.parametrize("mode", ["ask", "allow-all"])
async def test_actual_sdk_structured_plan_read_only_and_explicit_execution_retains_policy(tmp_path, mode):
    session, contexts = create(tmp_path, mode)
    try:
        await execute_command("plan", "start Implement", session)
        await session.send_message("build")
        assert session.plan.state.revision == 2 and session.plan.state.mode == "planning"
        assert "host-owned mode" in str(contexts[0].system_prompt)
        assert len(session.plan.state.steps) == 2
        await session.send_message("write")
        assert not (tmp_path / "target").exists()
        with pytest.raises(ValueError, match="changed"):
            await execute_command("plan", "execute 1", session)
        await execute_command("plan", "execute 2", session)
        assert session.permissions.mode == mode and session.plan.state.mode == "executing"
        await session.send_message("write")
        assert (tmp_path / "target").exists() == (mode == "allow-all")
        loaded = session.session_manager.load_session(session.session_id)
        assert loaded.plan["mode"] == "executing"
        branch = session.create_branch()
        await execute_command("plan", "revise", session)
        assert session.restore_branch(branch) and session.plan.state.mode == "executing"
        old = session.plan
        session.new_session()
        assert session.plan.state.mode == "off"
        with pytest.raises(RuntimeError, match="closed"):
            old.change("stop", old.state.revision)
        assert not (tmp_path / "target").exists() or mode == "allow-all"
    finally:
        session.dispose()


def test_live_ui_plan_confirmation_ownership_draft_and_approval_focus(tmp_path):
    from xdog.coding.core.permissions import PermissionRequest
    session, contexts = create(tmp_path)
    ui = InteractiveMode(session)
    def wait(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            ui._poll()
            if predicate():
                return
            time.sleep(0.002)
        raise AssertionError("Plan UI did not settle")
    try:
        ui._editor.set_text("preserved draft")
        ui._handle_slash_command("plan", "start Implement")
        wait(lambda: ui._command is None)
        panel = ui._plan_panel
        assert panel is not None and not contexts and ui._editor.get_text() == "preserved draft"
        asyncio.run(session.plan.tool().execute("update", {"expected_revision": 1, "steps": [
            {"description": "Implement", "status": "pending"}]}))
        ui._poll()
        panel.render(80)
        panel.handle_input(KeyEvent("e"))
        panel.handle_input(KeyEvent("enter"))
        assert session.plan.state.mode == "planning" and ui._command is None
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        wait(lambda: ui._command is None)
        assert session.plan.state.mode == "executing" and session.permissions.mode == "ask" and not contexts
        ui._show_permission_request(PermissionRequest("req", "bash", {}, "Approve?"))
        ui._open_transcript()
        assert ui._plan_panel is panel and ui._tui._focused is ui._permission_prompt
        ui._permission_prompt._on_decision("deny")
        assert ui._tui._focused is panel
        ui._output_epoch += 1
        ui._poll()
        assert ui._plan_panel is None and ui._tui._focused is ui._editor
    finally:
        ui._request_exit()
        session.dispose()


def test_invalid_saved_plan_cannot_drop_active_read_only_state(tmp_path):
    session, _ = create(tmp_path)
    try:
        session.plan_control("start", 0, "Objective")
        target = session.session_manager.create_session(working_dir=tmp_path)
        target.plan = {"mode": "bad", "revision": 0}
        session.session_manager.save_session(target)
        assert not session.switch_session(target.session_id)
        assert session.plan.state.mode == "planning"
    finally:
        session.dispose()
