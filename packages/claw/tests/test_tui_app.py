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
    assert [strip_ansi(row).rstrip() for row in log.render(100)] == [
        "", "   unknown · unknown", " › user input", "",
    ]
    log.start_assistant("", "run")
    log.update_assistant("live reply", "run")
    log.finalize_assistant("final reply", "run")
    log.add_user("next input")
    log.add_assistant("replayed reply")
    assert [strip_ansi(row).rstrip() for row in log.render(100)] == [
        "", "   unknown · unknown", " › user input", "", "",
        "   agent · unknown → 未知", " • final reply", "", "",
        "   unknown · unknown", " › next input", "", "",
        "   agent · unknown → 未知", " • replayed reply", "",
    ]
    assert [record.body for record in log.transcript_records()] == [
        "user input", "final reply", "next input", "replayed reply",
    ]


def test_user_background_includes_role_and_spacing_without_coloring_next_message() -> None:
    from xdog.claw.channels.tui.tui_app import ChatLog
    from xdog.tui.utils import string_width

    log = ChatLog()
    log.add_user("hello **world**")
    log.add_assistant("reply")
    rows = log.render(100)
    assert [strip_ansi(row).rstrip() for row in rows] == [
        "", "   unknown · unknown", " › hello world", "", "",
        "   agent · unknown → 未知", " • reply", "",
    ]
    for row in rows[:4]:
        assert row.startswith("\x1b[48;")
        assert row.endswith("\x1b[49m")
        assert string_width(row) == 100
    assert "\x1b[48;" not in ''.join(rows[4:])


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


def test_claw_model_label_combines_provider_and_thinking_and_updates_live() -> None:
    from xdog.claw.channels.tui.tui_app import ChatApp
    from xdog.tui.utils import string_width

    app = ChatApp('/tmp/unused-claw-test.sock')
    app._state.update(model='copilot/gpt-6.1-sol', thinking='medium')
    app._update_footer()
    assert 'copilot/gpt-6.1-sol - medium' in strip_ansi(app._footer.render(180)[0])
    assert 'thinking:' not in app._status_metadata()
    app._state.update(model='antigravity/vendor/model', thinking='off')
    app._update_footer()
    assert 'antigravity/vendor/model - off' in strip_ansi(app._footer.render(180)[0])
    assert 'copilot/' not in app._status_metadata()
    for level in ('unknown', None):
        app._state['thinking'] = level
        app._update_footer()
        assert ' | antigravity/vendor/model | ' in app._status_metadata()
        assert ' - unknown' not in strip_ansi(app._footer.render(180)[0])
    assert string_width(app._footer.render(42)[0]) <= 42


def test_claw_highlights_registered_command_in_input_not_arguments() -> None:
    from xdog.claw.channels.tui.tui_app import CustomEditor

    editor = CustomEditor()
    editor.set_autocomplete_visible(False)
    editor.set_text('/help model')
    assert any(editor._theme.accent('/help') + ' model' in row for row in editor.render(80))
    assert editor.get_text() == '/help model'


def test_system_notices_use_the_same_dot_and_gutter_without_markdown_conversion():
    from xdog.claw.channels.tui.tui_app import ChatLog

    log = ChatLog()
    log.add_system('session_ready *literal*\nsecond line', source='history')
    rows = [strip_ansi(row).rstrip() for row in log.render(100)]
    assert rows == ['', '   system · history', ' • session_ready *literal*', '   second line', '']
    record = log.transcript_records()[0]
    assert record.role == 'system' and record.body == 'session_ready *literal*\nsecond line'
    assert '•' not in record.body  # Presentation never rewrites the saved/source text.


def test_wrapped_system_notices_align_and_fit_narrow_terminals():
    from xdog.claw.channels.tui.tui_app import ChatLog
    from xdog.tui.utils import string_width

    log = ChatLog()
    log.add_system('first line wraps across several rows\nnext line\x1b[2J')
    rows = [strip_ansi(row).rstrip() for row in log.render(16)]
    marker = next(index for index, row in enumerate(rows) if row.startswith(' • '))
    assert all(not row or row.startswith('   ') for row in rows[marker + 1:])
    assert sum(row.startswith(' • ') for row in rows) == 1
    assert '\x1b' not in log.transcript_records()[0].body
    for width in (0, 1, 2, 3, 4, 8, 16):
        assert all(string_width(row) <= width for row in log.render(width))


def test_handoff_shortcut_hint_is_dim_and_not_part_of_the_message_body():
    from xdog.claw.channels.tui.tui_app import HandoffMessage, theme_dim

    message = HandoffMessage('full original handoff', 'Context handoff · compact',
                             {'role': 'system', 'source': 'history'})
    rows = message.render(100)
    hint = next(row for row in rows if 'Ctrl+O' in strip_ansi(row))
    assert theme_dim('Ctrl+O: full handoff') in hint
    assert 'Ctrl+O' not in message.detail_body
    message.set_expanded(True)
    assert 'Ctrl+O' not in _rendered(message)


def test_connection_notices_and_banner_follow_all_history_chunks():
    from xdog.claw.channels.tui.tui_app import ChatApp

    app = ChatApp('/tmp/unused-history-order.sock', group_id='maotao')
    try:
        app._handle_response({'type': 'pong', 'session_id': 'current', 'turn_count': 2,
                              'history_count': 2, 'history_format': 2, 'session_controls': 1})
        assert 'Resumed context' not in _rendered(app._chat_log)
        assert 'Connected · group' not in _rendered(app._chat_log)
        for text in ('FIRST-HISTORICAL-MESSAGE', 'LAST-HISTORICAL-MESSAGE'):
            app._handle_response({'type': 'history_chunk', 'entries': [{'role': 'user', 'content': text}]})
        app._handle_response({'type': 'history_end'})
        output = _rendered(app._chat_log)
        assert output.index('LAST-HISTORICAL-MESSAGE') < output.index('Resumed context')
        assert output.index('Resumed context') < output.index('Connected · group')
        assert 'unix://' not in output
        details = '\n'.join(record.body for record in app._chat_log.detail_records())
        assert 'unused-history-order.sock' in details and 'current' in details
        app._handle_response({'type': 'history_end'})
        assert _rendered(app._chat_log).count('Connected · group') == 1
    finally:
        app._request_exit()


def test_reconnect_banner_is_after_refreshed_history_and_does_not_duplicate_startup_notices():
    from xdog.claw.channels.tui.tui_app import ChatApp

    app = ChatApp('/tmp/unused-history-order.sock')
    try:
        app._handle_response({'type': 'pong', 'session_id': 'current', 'history_count': 0, 'session_controls': 1})
        assert _rendered(app._chat_log).count('New context') == 1
        app._handle_response({'type': '_connection_opened', 'generation': 1})
        app._handle_response({'type': 'pong', 'session_id': 'current', 'history_count': 1,
                              'history_format': 2, 'session_controls': 1})
        app._handle_response({'type': 'history_chunk', 'entries': [{'role': 'user', 'content': 'LATEST-HISTORY'}]})
        assert 'Connected · group' not in _rendered(app._chat_log)
        app._handle_response({'type': 'history_end'})
        output = _rendered(app._chat_log)
        assert (output.index('LATEST-HISTORY') < output.index('Current group context loaded')
                < output.index('Connected · group'))
        assert 'New context' not in output and 'Resumed context' not in output
    finally:
        app._request_exit()


def test_system_dots_use_accent_while_agent_dots_and_shortcuts_remain_dim():
    from xdog.claw.channels.tui.tui_app import (
        ExternalMessage,
        HandoffMessage,
        SystemMessage,
        _AssistantText,
        theme_accent,
        theme_dim,
    )

    messages = [SystemMessage('notice', 'tui'), ExternalMessage('event', {'role': 'system', 'source': 'acp'}),
                HandoffMessage('full handoff', 'compact handoff', {'role': 'system', 'source': 'history'})]
    for message in messages:
        output = '\n'.join(message.render(100))
        assert theme_accent('•') in output and theme_dim('•') not in output
    assert theme_dim('•') in '\n'.join(_AssistantText('answer', {}).render(100))
    hint = next(row for row in messages[-1].render(100) if 'Ctrl+O' in strip_ansi(row))
    assert theme_dim('Ctrl+O: full handoff') in hint
