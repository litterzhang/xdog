"""Tests for retained Claw TUI history."""

from xdog.tui.keys import KeyEvent
from xdog.tui.utils import strip_ansi


def _rendered(component: object) -> str:
    render = getattr(component, "render")
    return "\n".join(strip_ansi(line) for line in render(100))


def test_chat_log_does_not_prune_completed_history_at_fixed_count() -> None:
    from xdog.claw.channels.tui.tui_app import ChatLog

    log = ChatLog()
    for index in range(181):
        log.add_assistant(f"CLAW-HISTORY-{index}")

    rendered = _rendered(log)
    assert "CLAW-HISTORY-0" in rendered
    assert "CLAW-HISTORY-180" in rendered


def test_chat_log_renders_structured_tool_result_with_detail_toggle() -> None:
    from xdog.claw.channels.tui.tui_app import ChatLog

    log = ChatLog()
    tool = log.add_tool("bash", {"command": "pytest -q"}, tool_call_id="call-1")
    tool.set_result(f"first line\n{'x' * 600}\nCLAW-TOOL-RESULT-TAIL")

    log.set_details_expanded(False)
    collapsed = _rendered(log)
    assert "• Ran pytest -q" in collapsed
    assert "2 hidden lines" in collapsed
    assert "└ first line" in collapsed
    assert "CLAW-TOOL-RESULT-TAIL" not in collapsed

    log.set_details_expanded(True)
    expanded = _rendered(log)
    assert "CLAW-TOOL-RESULT-TAIL" in expanded


def test_assistant_reasoning_strips_terminal_control_sequences() -> None:
    from xdog.claw.channels.tui.tui_app import AssistantMessage

    message = AssistantMessage(
        "answer",
        thinking="safe\x1b[2J\x1b]52;c;clipboard\x07tail",
    )
    message.set_expanded(True)
    rendered = _rendered(message)

    assert "safetail" in rendered
    assert "\x1b[2J" not in rendered
    assert "\x1b]52" not in rendered


def test_chat_app_preserves_assistant_tool_assistant_order() -> None:
    from xdog.claw.channels.tui.tui_app import ChatApp

    app = ChatApp("/tmp/unused-claw-test.sock")
    app._active_run_id = "run-1"
    app._handle_response({"type": "delta", "run_id": "run-1", "content": "PRE-TOOL"})
    app._handle_response({
        "type": "tool_call", "run_id": "run-1", "id": "call-1", "name": "bash", "arguments": {},
    })
    app._handle_response({
        "type": "tool_result", "run_id": "run-1", "id": "call-1", "name": "bash", "result": "TOOL-RESULT",
    })
    app._handle_response({"type": "delta", "run_id": "run-1", "content": "POST-TOOL"})

    rendered = _rendered(app._chat_log)
    assert rendered.index("PRE-TOOL") < rendered.index("TOOL-RESULT") < rendered.index("POST-TOOL")


def test_reused_tool_call_id_appends_a_new_retained_component() -> None:
    from xdog.claw.channels.tui.tui_app import ChatLog

    log = ChatLog()
    first = log.add_tool("bash", {}, tool_call_id="call-1")
    log.finish_tool("call-1", "bash", "FIRST")
    second = log.add_tool("bash", {}, tool_call_id="call-1")
    log.finish_tool("call-1", "bash", "SECOND")

    assert first is not second
    rendered = _rendered(log)
    assert rendered.index("FIRST") < rendered.index("SECOND")


def test_empty_tool_result_is_completed() -> None:
    from xdog.claw.channels.tui.tui_app import ChatLog

    log = ChatLog()
    tool = log.add_tool("bash", {"command": "true"}, tool_call_id="call-empty")
    tool.set_result("")
    rendered = _rendered(log)

    assert "• Ran true" in rendered
    assert "(no output)" in rendered


def test_chat_app_consumes_structured_tool_events_and_ctrl_o() -> None:
    from xdog.claw.channels.tui.tui_app import ChatApp

    app = ChatApp("/tmp/unused-claw-test.sock")
    app._active_run_id = "run-1"
    app._handle_response({
        "type": "tool_call",
        "run_id": "run-1",
        "id": "call-1",
        "name": "bash",
        "arguments": {"command": "pytest -q"},
    })
    app._handle_response({
        "type": "tool_result",
        "run_id": "run-1",
        "id": "call-1",
        "name": "bash",
        "result": f"first\n{'x' * 600}\nSTRUCTURED-TAIL",
        "is_error": False,
    })

    assert "STRUCTURED-TAIL" not in _rendered(app._chat_log)
    assert app._handle_global_input(KeyEvent(key="o", ctrl=True)) == {"consume": True}
    assert "STRUCTURED-TAIL" not in _rendered(app._chat_log)
    assert app._layout.details is app._details_panel
    assert app._details_panel.render(100)[1] == "first"
    app._details_panel.handle_input(KeyEvent(key="end"))
    assert "STRUCTURED-TAIL" in "\n".join(app._details_panel.render(100))


def test_claw_uses_shared_user_spacing_and_role_markers_for_live_and_replayed_messages():
    from xdog.claw.channels.tui.tui_app import ChatLog

    log = ChatLog()
    log.add_user("user input")
    assert [strip_ansi(row).rstrip() for row in log.render(40)] == ["", " › user input", ""]
    log.start_assistant("", "run")
    log.update_assistant("live reply", "run")
    log.finalize_assistant("final reply", "run")
    log.add_user("next input")
    log.add_assistant("replayed reply")
    assert [strip_ansi(row).rstrip() for row in log.render(40)] == [
        "", " › user input", "", "", " • final reply", "", "", " › next input", "", "", " • replayed reply", "",
    ]
    assert [record.body for record in log.transcript_records()] == [
        "user input", "final reply", "next input", "replayed reply",
    ]


def test_user_background_includes_role_and_spacing_without_coloring_next_message() -> None:
    from xdog.claw.channels.tui.tui_app import ChatLog
    from xdog.tui.utils import visible_width

    log = ChatLog()
    log.add_user("hello **world**")
    log.add_assistant("reply")
    rows = log.render(40)
    assert [strip_ansi(row).rstrip() for row in rows] == ["", " › hello world", "", "", " • reply", ""]
    for row in rows[:3]:
        assert row.startswith("\x1b[48;")
        assert row.endswith("\x1b[49m")
        assert visible_width(row) == 40
    assert "\x1b[48;" not in rows[-2] + rows[-1]


def test_claw_status_and_hints_use_model_dim_color_after_updates() -> None:
    from xdog.claw.channels.tui.tui_app import ChatApp, theme_dim

    app = ChatApp("/tmp/unused-claw-test.sock")
    app._footer.set_fields(activity="ready", model="model-name", context="ctx:1k/32k")
    app._hints.set_text("Enter send")
    for component in (app._footer, app._hints):
        row = component.render(80)[0]
        assert row == theme_dim(strip_ansi(row))


def test_claw_status_and_shortcuts_align_with_input_text() -> None:
    from xdog.claw.channels.tui.tui_app import ChatApp

    app = ChatApp("/tmp/unused-claw-test.sock")
    app._editor.focused = False
    app._editor.set_text("INPUT")
    app._footer.set_fields(activity="ready", model="model-name")
    app._update_hints()
    rows = [strip_ansi(row) for row in app._layout.render(100)]
    assert next(row for row in rows if "ready" in row).index("ready") == 2
    assert next(row for row in rows if "INPUT" in row).index("INPUT") == 2
    assert next(row for row in rows if "Enter send" in row).index("Enter send") == 2
