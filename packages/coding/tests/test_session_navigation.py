from __future__ import annotations

import time

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.history import history_points
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent


def session(tmp_path):
    workspace = tmp_path / "first"
    workspace.mkdir()
    manager = SessionManager(tmp_path / "sessions")
    data = manager.create_session(model="test/dummy", working_dir=workspace)
    agent = Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))
    agent.replace_messages([UserMessage(content="original"), AssistantMessage(content=(TextContent(text="answer"),))])
    result = AgentSession(agent=agent, session_data=data, session_manager=manager,
                          settings=SettingsManager(global_path=tmp_path / "global.json", project_dir=workspace),
                          tool_registry=None, bash=None, working_dir=workspace)
    result._persist()
    return result


def test_switch_restores_workspace_model_thinking_resets_session_rules_and_keeps_old_head(tmp_path):
    current = session(tmp_path)
    target_dir = tmp_path / "second"
    target_dir.mkdir()
    target = current.session_manager.create_session(model="test/other", working_dir=target_dir)
    target.messages = [UserMessage(content="target history")]
    target.settings = {"thinking_level": "high", "custom_instructions": "second session only"}
    current.session_manager.save_session(target)
    original = current.session_id
    current.settings.set_session_instructions("first only")
    mode = current.permissions.mode
    assert current.switch_session(target.session_id)
    assert current.working_dir == target_dir
    assert current.agent.tool_context["fs_workspace"] == str(target_dir)
    assert current.model == "test/other"
    assert current.agent.options.thinking == "high"
    assert current.permissions.mode == mode
    assert current.messages[0].content[0].text == "target history"
    assert "second session only" in current.settings.custom_instructions
    assert "first only" not in current.settings.custom_instructions
    assert current.session_manager.load_session(original).messages[0].content[0].text == "original"
    # Same-session activation is a no-op and cannot reload stale disk history.
    current.agent.append_message(UserMessage(content="live unsaved"))
    assert current.switch_session(current.session_id)
    assert current.messages[-1].content == "live unsaved"


def test_branches_are_reversible_and_tool_batches_are_not_split(tmp_path):
    current = session(tmp_path)
    messages = [UserMessage(content="original"), AssistantMessage(content=(
        ToolCall(id="one", name="filesystem", arguments={}), ToolCall(id="two", name="filesystem", arguments={}),
    )), ToolResultMessage(tool_call_id="one", tool_name="filesystem", content=(TextContent(text="one"),)),
        ToolResultMessage(tool_call_id="two", tool_name="filesystem", content=(TextContent(text="two"),)),
        AssistantMessage(content=(TextContent(text="finished"),))]
    current.agent.replace_messages(messages)
    assert [point.index for point in history_points(messages)] == [0, 3, 4]
    with pytest.raises(ValueError, match="batch"):
        current.create_branch(at_index=1)
    branch = current.create_branch(at_index=3)
    assert current.restore_branch(branch)
    assert len(current.messages) == 4
    backups = [data for data in current.session_data.branches if data.get("label", "").startswith("Return")]
    assert len(backups) == 1
    assert current.restore_branch(backups[0]["branch_id"])
    assert len(current.messages) == 5
    assert current.session_data.active_branch == backups[0]["branch_id"]
    assert current.session_data.branches[-1]["parent_branch"] == branch


def test_session_id_traversal_and_symlink_files_are_refused(tmp_path):
    current = session(tmp_path)
    for key in ("../../outside", "/outside", "..", ""):
        with pytest.raises(ValueError):
            current.session_manager.load_session(key)
    path = current.session_manager._session_path("link")
    path.symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symbolic"):
        current.session_manager.load_session("link")


def test_live_browser_search_tree_and_activation_retains_draft_and_visible_context(tmp_path):
    current = session(tmp_path)
    target = current.session_manager.create_session(model="test/dummy", summary="SEARCH-TARGET",
                                                    working_dir=current.working_dir)
    target.messages = [UserMessage(content="RESTORED-HISTORY")]
    current.session_manager.save_session(target)
    ui = InteractiveMode(current)
    try:
        ui._editor.set_text("preserved draft")
        ui._open_navigation()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            ui._poll()
            if not ui._navigation_panel._view.busy:
                break
            time.sleep(0.005)
        ui._tui._dispatch_input(KeyEvent("/"))
        ui._tui._dispatch_paste("SEARCH-TARGET")
        while time.monotonic() < deadline:
            ui._poll()
            if not ui._navigation_panel._view.busy:
                break
            time.sleep(0.005)
        assert len(ui._navigation_panel._view.entries) == 1
        ui._tui._dispatch_input(KeyEvent("enter"))  # finish filter
        ui._tui._dispatch_input(KeyEvent("enter"))  # activate through owned command
        while time.monotonic() < deadline and ui._command is not None:
            ui._poll()
            time.sleep(0.005)
        ui._poll()
        assert ui._session.session_id == target.session_id
        assert ui._navigation_panel is None
        assert ui._editor.get_text() == "preserved draft"
        assert "RESTORED-HISTORY" in "\n".join(ui._chat_log.render(80))
        assert ui._editor.focused
    finally:
        ui._request_exit()
