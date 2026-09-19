from __future__ import annotations

from unittest.mock import MagicMock

from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.components.bounded_details import BoundedDetails, DetailRecord
from xdog.tui.keys import KeyEvent
from xdog.tui.utils import strip_ansi


def mode():
    session = MagicMock()
    session.agent.subscribe.return_value = lambda: None
    session.agent.options.thinking = "medium"
    session.permissions.mode = "ask"
    session.model = "copilot/a-very-long-model-name"
    session.session_id = "session-test"
    session.messages = []
    session.working_dir = "/workspace"
    session.context_limit = 1000
    return InteractiveMode(session)


def plain(component, width=100):
    return "\n".join(strip_ansi(row) for row in component.render(width))


def test_details_command_focus_and_empty_feedback():
    app = mode()
    app._editor.set_text("draft")
    app._handle_slash_command("details", "")
    assert app._tui._focused is app._details_panel
    assert "No details" in plain(app._layout)
    app._tui._dispatch_input(KeyEvent(key="escape"))
    assert app._tui._focused is app._editor
    assert app._editor.get_text() == "draft"


def test_details_below_status_and_editor():
    app = mode()
    app._chat_log.add_assistant("answer", thinking="REASONING")
    app._handle_global_input(KeyEvent(key="o", ctrl=True))
    rows = plain(app._layout).splitlines()
    assert rows.index("REASONING") > next(i for i, row in enumerate(rows) if row.startswith("ready"))
    assert rows.index("REASONING") > next(i for i, row in enumerate(rows) if row.startswith(">"))


def test_reasoning_starts_at_top_and_log_scroll_stays_paused():
    records = [DetailRecord("reason", "\n".join(f"reason-{i}" for i in range(20)), "reasoning")]
    panel = BoundedDetails(lambda: records, max_rows=4)
    assert "reason-0" in plain(panel)
    panel.handle_input(KeyEvent(key="pagedown"))
    assert "reason-0" not in plain(panel)

    records[:] = [DetailRecord("log", "\n".join(f"log-{i}" for i in range(20)), "tool")]
    panel.show_latest()
    assert "log-19" in plain(panel)
    panel.handle_input(KeyEvent(key="pageup"))
    before = panel.render(100)[1:]
    records[0] = DetailRecord("log", records[0].body + "\nlog-20\nlog-21", "tool")
    assert panel.render(100)[1:] == before
    panel.handle_input(KeyEvent(key="end"))
    assert "log-21" in plain(panel)


def test_activity_tracks_tools_and_generation_and_clears(monkeypatch):
    app = mode()
    monkeypatch.setattr("xdog.coding.modes.interactive.interactive_mode.time.monotonic", lambda: 100.0)
    app._set_busy(True)
    app._handle_ui_event({"type": "tool_call", "id": "one", "name": "bash", "arguments": {}})
    app._poll()
    assert "running bash" in plain(app._footer)
    app._handle_ui_event({"type": "tool_result", "id": "one", "result": "ok"})
    app._handle_ui_event({"type": "text_update", "text": "answer"})
    app._poll()
    assert "responding" in plain(app._footer)
    monkeypatch.setattr("xdog.coding.modes.interactive.interactive_mode.time.monotonic", lambda: 108.0)
    assert app._format_elapsed() == "8s"
    app._handle_ui_event({"type": "turn_end"})
    app._poll()
    assert "ready" in plain(app._footer)
    assert app._busy_started is None


def test_short_status_keeps_context_and_queue_before_long_model():
    app = mode()
    app._footer.update(model="copilot/" + "m" * 60, context_tokens=250, max_context=1000)
    app._footer.set_queue_count(2)
    result = plain(app._footer, 42)
    assert "ctx:250/1.0k" in result
    assert "queued 2" in result
    assert "ready" in result


def test_no_color_theme_and_ascii_tool_markers(monkeypatch):
    from xdog.coding.modes.interactive.components.tool_execution import ToolExecutionComponent
    from xdog.coding.modes.interactive.theme import create_default_theme
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("XDOG_TUI_ASCII", "1")
    theme = create_default_theme()
    tool = ToolExecutionComponent("bash", {}, theme)
    tool.set_result("ok")
    result = "\n".join(tool.render(80))
    assert "\x1b[" not in result
    assert "[ok]" in result
    assert "success" in result


def test_tool_duration_is_frozen_after_completion(monkeypatch):
    from xdog.coding.modes.interactive.components.tool_execution import ToolExecutionComponent
    from xdog.coding.modes.interactive.theme import create_default_theme
    monkeypatch.setattr("time.monotonic", lambda: 100.0)
    tool = ToolExecutionComponent("bash", {}, create_default_theme())
    monkeypatch.setattr("time.monotonic", lambda: 104.0)
    tool.set_result("ok")
    monkeypatch.setattr("time.monotonic", lambda: 200.0)
    assert "4s" in plain(tool)
    assert "100s" not in plain(tool)


def test_light_theme_uses_terminal_foreground(monkeypatch):
    from xdog.coding.modes.interactive.theme import create_default_theme
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("XDOG_TUI_THEME", "light")
    theme = create_default_theme()
    assert theme.fg("text") == "text"
    assert strip_ansi(theme.user_bg("text")) == "text"
    assert theme.user_bg("text") != "text"
    assert "\x1b[" in theme.accent("selected")


def test_permission_summary_has_position_and_precise_session_scope():
    from xdog.coding.core.permissions import PermissionRequest
    from xdog.coding.modes.interactive.components.permission_prompt import PermissionPromptComponent
    from xdog.coding.modes.interactive.theme import create_default_theme
    prompt = PermissionPromptComponent(
        PermissionRequest(id="r", tool_name="bash", arguments={}, summary="\n".join(f"command-{i}" for i in range(40))),
        create_default_theme(), lambda decision: None,
    )
    prompt.set_render_budget(12)
    first = plain(prompt)
    assert "/40" in first
    assert "same call" in first
    assert "command-0" in first
    prompt.handle_input(KeyEvent(key="pagedown"))
    assert plain(prompt) != first
    assert len(prompt.render(100)) <= 12


def test_open_details_tracks_new_records_until_user_scrolls():
    records = [DetailRecord("reason", "why", "reasoning")]
    panel = BoundedDetails(lambda: records)
    panel.show_latest()
    assert "why" in plain(panel)
    records.append(DetailRecord("bash", "tool output", "tool"))
    assert "tool output" in plain(panel)
    panel.handle_input(KeyEvent(key="home"))
    records.append(DetailRecord("next", "new output", "tool"))
    assert "tool output" in plain(panel)


def test_ctrl_c_leaves_details_without_losing_draft():
    app = mode()
    app._editor.set_text("draft")
    app._handle_slash_command("details", "")
    assert app._tui._dispatch_input(KeyEvent(key="c", ctrl=True))
    assert app._tui._focused is app._editor
    assert app._editor.get_text() == "draft"


def test_terminal_error_releases_permission_focus():
    from xdog.coding.core.permissions import PermissionRequest
    app = mode()
    app._set_busy(True)
    app._show_permission_request(PermissionRequest(id="r", tool_name="bash", arguments={}, summary="cmd"))
    app._handle_ui_event({"type": "error", "message": "failed"})
    assert app._permission_prompt is None
    assert app._tui._focused is app._editor
    assert not app._awaiting_permission
    assert not app._is_busy


def test_compact_tool_uses_two_rows_and_retains_full_details():
    from xdog.coding.modes.interactive.components.tool_execution import ToolExecutionComponent
    from xdog.coding.modes.interactive.theme import create_default_theme
    from xdog.tui.utils import visible_width
    tool = ToolExecutionComponent("bash", {"command": "echo " + "界" * 80}, create_default_theme())
    assert len(tool.render(36)) == 3
    output = "\n".join(f"line-{i}" for i in range(30)) + "\nLATEST"
    tool.set_streaming(output)
    rows = tool.render(36)
    assert len(rows) == 3
    assert all(visible_width(row) <= 36 for row in rows)
    assert "LATEST" in plain(tool, 36)
    tool.set_result(output)
    assert len(tool.render(36)) == 3
    assert tool.detail_body == output
    tool.set_expanded(True)
    assert "line-0" in plain(tool, 80)



def test_permission_has_box_and_keeps_all_choices_at_normal_height():
    from xdog.coding.core.permissions import PermissionRequest
    app = mode()
    app._show_permission_request(PermissionRequest(id="r", tool_name="bash", arguments={}, summary="echo hello"))
    prompt = app._permission_prompt
    rows = [strip_ansi(row) for row in prompt.render(80)]
    assert rows[0].startswith("╭")
    assert rows[-1].startswith("╰")
    assert "echo hello" in "\n".join(rows)
    assert all(word in "\n".join(rows) for word in ("Allow once", "same call", "Deny"))
    assert len(rows) <= 12


def test_ansi16_theme_avoids_truecolor_and_256color_sequences(monkeypatch):
    from xdog.coding.modes.interactive.theme import create_default_theme
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("XDOG_TUI_COLOR", "16")
    theme = create_default_theme()
    for style in (theme.accent, theme.error, theme.tool, theme.markdown.code):
        result = style("content")
        assert "\x1b[" in result
        assert "38;2;" not in result
        assert "38;5;" not in result


def test_light_semantic_colors_have_readable_contrast(monkeypatch):
    import re

    from xdog.coding.modes.interactive.theme import create_default_theme
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("XDOG_TUI_THEME", "light")
    monkeypatch.setenv("XDOG_TUI_COLOR", "truecolor")
    theme = create_default_theme()
    for style in (theme.accent, theme.dim, theme.error, theme.success, theme.tool, theme.markdown.code):
        match = re.search(r"38;2;(\d+);(\d+);(\d+)m", style("text"))
        assert match is not None
        values = [int(value) / 255 for value in match.groups()]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
        luminance = sum(v * w for v, w in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))
        assert 1.05 / (luminance + 0.05) >= 4.5


def test_256color_theme_uses_palette_sequences(monkeypatch):
    from xdog.coding.modes.interactive.theme import create_default_theme
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.setenv("XDOG_TUI_COLOR", "auto")
    assert "38;5;" in create_default_theme().accent("text")


def test_permission_resume_preserves_parallel_tool_phase():
    from xdog.coding.core.permissions import PermissionRequest
    from xdog.coding.modes.interactive.run_status import RunPhase
    app = mode()
    app._set_busy(True)
    for call_id in ("first", "second"):
        app._handle_ui_event({"type": "tool_call", "id": call_id, "name": "bash", "arguments": {}})
    app._show_permission_request(PermissionRequest(id="r", tool_name="bash", arguments={}, summary="cmd"))
    app._handle_ui_event({"type": "tool_result", "id": "first", "result": "done"})
    assert app._run_status.phase == RunPhase.PERMISSION
    app._tui._dispatch_input(KeyEvent(key="enter"))
    assert app._run_status.phase == RunPhase.RUNNING
    app._poll()
    assert "running bash" in plain(app._footer)
    app._handle_ui_event({"type": "tool_result", "id": "second", "result": "done"})
    assert app._run_status.phase == RunPhase.WAITING


def test_worker_cancel_signal_reset_does_not_restart_ui_timer():
    app = mode()
    app._set_busy(True)
    app._cancel_active_work()
    before = plain(app._footer)
    # The worker clears its control flag before its terminal event is polled.
    app._cancel_requested = False
    app._poll()
    assert plain(app._footer) == before


def test_empty_context_capacity_and_model_remain_visible():
    app = mode()
    app._footer.update(model="copilot/gemini-3.5-flash", max_context=936_000)
    assert "ctx:0/936k" in plain(app._footer, 60)
    app._footer.set_activity("awaiting tool permission for a long running command")
    result = plain(app._footer, 60)
    assert "ctx:0/936k" in result
    assert "gemini" in result


def test_disk_resume_replays_user_blocks_and_preserves_usage_and_model(tmp_path, monkeypatch):
    import xdog.ai as ai
    from xdog.ai.types import AssistantMessage, Model, TextContent, Usage, UserMessage
    from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session

    provider = MagicMock()
    model = Model(id="copilot/saved-model", provider="copilot", context_window=936_000)
    provider.model.return_value = model
    provider.models.return_value = (model,)
    monkeypatch.setattr(ai, "provider", lambda _: provider)
    monkeypatch.setattr(ai, "load", lambda: provider)
    monkeypatch.setenv("CODING_DIR", str(tmp_path / "data"))
    created = create_agent_session(CreateSessionOptions(
        working_dir=tmp_path, overrides={"model": model.id},
    )).session
    created.agent.replace_messages([
        UserMessage(content="RESTORED USER PROMPT"),
        AssistantMessage(content=[TextContent(text="RESTORED ANSWER")],
                         usage=Usage(input=1234, cache_read=1000)),
    ])
    created._persist()
    created.dispose()
    restored = create_agent_session(CreateSessionOptions(working_dir=tmp_path, resume=True)).session
    try:
        assert restored.model == model.id
        app = InteractiveMode(restored)
        app._replay_history()
        app._update_footer()
        assert "RESTORED USER PROMPT" in plain(app._chat_log)
        assert "RESTORED ANSWER" in plain(app._chat_log)
        assert "2.2k" in plain(app._footer)
        assert "saved-model" in plain(app._footer)
        app._editor.handle_input(KeyEvent(key="up"))
        assert app._editor.get_text() == "RESTORED USER PROMPT"
    finally:
        restored.dispose()


def test_message_spacing_and_subtle_user_background(monkeypatch):
    from xdog.coding.modes.interactive.components.assistant_message import AssistantMessageComponent
    from xdog.coding.modes.interactive.components.user_message import UserMessageComponent
    from xdog.coding.modes.interactive.theme import create_default_theme

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    for theme_name in ("native", "light", "dark"):
        monkeypatch.setenv("XDOG_TUI_THEME", theme_name)
        theme = create_default_theme()
        for width in (24, 80):
            rows = UserMessageComponent("hello", theme).render(width)
            assert len(rows) == 2  # one separator plus one content row
            assert "\x1b[48;" in rows[1]
            rows += AssistantMessageComponent("reply", theme).render(width)
            text = [strip_ansi(row).strip() for row in rows]
            assert text.index("reply") - text.index("hello") == 2


def test_input_hint_prefers_ctrl_enter():
    app = mode()
    assert "Ctrl+Enter newline" in plain(app._hints)
    app._update_hints()
    assert "Ctrl+Enter newline" in plain(app._hints)


def test_thinking_text_is_visible_without_opening_details_and_survives_replay():
    import json

    from xdog.ai.types import AssistantMessage, ThinkingContent
    from xdog.coding.core.messages import dicts_to_messages, messages_to_dicts

    app = mode()
    text = "VISIBLE REASONING\nFULL DETAIL"
    messages = [AssistantMessage(content=[ThinkingContent(
        thinking=text,
        thinking_signature=json.dumps({"type": "reasoning", "id": "x" * 416}),
    )])]
    app._session.messages = dicts_to_messages(messages_to_dicts(messages))
    app._replay_history()
    assert "VISIBLE REASONING" in plain(app._chat_log)
    assert app._chat_log.detail_records()[0].body == text

    app._chat_log.update_assistant("", thinking="LIVE REASONING")
    assert "LIVE REASONING" in plain(app._chat_log)


def test_multiline_tool_summary_is_one_physical_row_and_result_leads():
    from xdog.coding.modes.interactive.components.tool_execution import ToolExecutionComponent
    from xdog.coding.modes.interactive.theme import create_default_theme
    tool = ToolExecutionComponent("bash", {"command": "uv run python - <<'PY'\nimport json\nprint('ok')\nPY"},
                                  create_default_theme())
    rows = tool.render(100)
    assert all("\n" not in row and "\r" not in row for row in rows)
    assert "Python script" in strip_ansi(rows[0])
    tool.set_result("Verified successfully\nmore\nlast")
    result = strip_ansi(tool.render(100)[1])
    assert result.index("Verified successfully") < result.index("hidden lines")


def test_thinking_preview_shows_body_without_markdown_and_bounds_rows():
    from xdog.coding.modes.interactive.components.assistant_message import AssistantMessageComponent
    from xdog.coding.modes.interactive.theme import create_default_theme
    component = AssistantMessageComponent("", create_default_theme(),
        thinking="**Reflecting on user task**\n\nCheck history conversion and usage. " * 30)
    component.set_expanded(False)
    rows = [strip_ansi(row).strip() for row in component.render(40)]
    assert "**" not in "\n".join(rows)
    assert "Check history" in "\n".join(rows)
    assert len([row for row in rows if row]) <= 3
