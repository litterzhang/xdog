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
                          tool_repo=None, working_dir=workspace)
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


def test_local_slash_invocations_pair_compact_results_without_saving_or_prompting(tmp_path):
    from xdog.tui.components.command_message import CommandMessage
    from xdog.tui.utils import string_width, strip_ansi

    current = session(tmp_path)
    ui = InteractiveMode(current)
    original_messages = tuple(current.messages)
    saved_path = current.session_manager._session_path(current.session_id)
    saved_bytes = saved_path.read_bytes()
    try:
        ui._handle_submit("/keys")
        keys = ui._chat_log.children[-1]
        assert isinstance(keys, CommandMessage) and keys.detail_title == "/keys"
        compact = keys.render(90)
        assert strip_ansi(compact[0]) == " • /keys"
        assert ui._theme.tool("•") in compact[0]
        assert len(compact) == 12 and "Ctrl+O" in strip_ansi(compact[-1])
        assert "Active keybindings" in keys.detail_body and "Ctrl+Enter" in keys.detail_body
        assert ui._chat_log.detail_records()[-1].body == keys.detail_body
        ui._open_details()
        assert "Active keybindings" in strip_ansi("\n".join(ui._details_panel.render(120)))
        ui._close_details()
        for width in (1, 3, 10, 90):
            assert all(string_width(row) <= width for row in keys.render(width))
        preview = CommandMessage("/example", ui._theme.panel_theme())
        preview.set_result("\n".join(f"result {i}" for i in range(11)))
        rows = [strip_ansi(row).strip() for row in preview.render(90)]
        assert rows[1:11] == [f"result {i}" for i in range(10)]
        assert rows[11] == "Ctrl+O: full command result"
        assert "result 10" in preview.detail_body
        preview.set_result("short result\nsecond line")
        assert [strip_ansi(row).strip() for row in preview.render(90)] == [
            "• /example", "short result", "second line"]
        preview.set_result("x" * 120)
        assert len(preview.render(13)) == 12  # Ten wrapped rows, not ten source lines.
        preview.set_expanded(True)
        assert len(preview.render(13)) == 13  # Full output remains available.
        # The worker result updates the original invocation, even after newer UI output.
        ui._handle_submit("/name")
        invocation = ui._command
        assert invocation is not None
        ui._chat_log.add_system("an unrelated notice")
        ui._command_thread.join(3)
        assert not ui._command_thread.is_alive()
        ui._event_queue.drain(ui._handle_ui_event)
        assert ui._command is None
        assert invocation.display.detail_title == "/name"
        assert "Session name:" in invocation.display.detail_body
        assert ui._chat_log.children.count(invocation.display) == 1
        stale = invocation.display.detail_body
        ui._handle_ui_event({"type": "command_result", "command_id": invocation.id, "output": "stale"})
        assert invocation.display.detail_body == stale
        assert tuple(current.messages) == original_messages
        assert saved_path.read_bytes() == saved_bytes
        ui._chat_log.clear_all()
        ui._replay_history()
        assert not any(isinstance(child, CommandMessage) for child in ui._chat_log.children)
    finally:
        ui._request_exit()
