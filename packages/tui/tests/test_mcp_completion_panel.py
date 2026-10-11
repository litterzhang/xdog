from __future__ import annotations

import json

from xdog.tui.components.mcp_panel import McpPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.utils import visible_width


def form(kind="prompt"):
    sent = []
    panel = McpPanel(lambda *args: sent.append(args), lambda: None, lambda: None, lambda: None)
    panel.focused = True
    panel.publish("list", rows=({"server": "peer", "state": "connected"},))
    panel.handle_input(KeyEvent("p" if kind == "prompt" else "t"))
    name = "review" if kind == "prompt" else "fixture://{name}{?format}"
    args = (
        (("topic", "", True), ("language", "", False))
        if kind == "prompt"
        else (("name", "", False), ("format", "", False))
    )
    panel.publish("prompts" if kind == "prompt" else "templates", rows=({"name": name, "arguments": args},))
    panel.handle_input(KeyEvent("enter"))
    panel.handle_input(KeyEvent("space", ctrl=True))
    return panel, sent


def reply(kind="prompt", argument="topic", value="", context=()):
    return {
        "server": "peer",
        "kind": kind,
        "name": "review" if kind == "prompt" else "fixture://{name}{?format}",
        "argument": argument,
        "value": value,
        "context": context,
        "values": ("completed value", "alternative"),
        "total": 5,
        "has_more": True,
    }


def test_prompt_completion_explicit_tab_selection_updates_json_only_after_visible_enter():
    panel, sent = form()
    try:
        assert panel._mode == "completion" and sent[-1][0] == "prompts"
        panel.handle_input(KeyEvent("tab"))
        assert sent[-1] == ("complete_prompt", "peer", "review", {"argument": "topic", "value": "", "context": "{}"})
        panel.publish("complete_prompt", rows=(reply(),))
        panel.handle_input(KeyEvent("enter"))
        assert panel._mode == "completion"  # Not painted, no implicit acceptance.
        panel.render(80)
        panel.handle_input(KeyEvent("down"))
        panel.handle_input(KeyEvent("enter", event_type=KeyEventType.REPEAT))
        assert panel._mode == "completion"
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        assert panel._mode == "arguments"
        assert json.loads(panel._arguments.get_text()) == {"topic": "alternative"}
        assert sent[-1][0] == "complete_prompt"  # Does not retrieve or execute.
        panel.handle_input(KeyEvent("enter", event_type=KeyEventType.REPEAT))
        assert sent[-1][0] == "complete_prompt"  # Held Enter cannot retrieve after accepting a suggestion.
        panel.handle_input(KeyEvent("enter"))
        assert sent[-1] == ("prompt", "peer", "review", {"topic": "alternative"})
    finally:
        panel.close()


def test_edit_cancel_and_scope_mismatch_cannot_insert_late_values_or_erase_parent_draft():
    panel, sent = form()
    try:
        panel.handle_input(KeyEvent("tab"))
        panel.handle_paste("new prefix")
        assert sent[-1][0] == "completion_cancel" and not panel._busy
        panel.publish("complete_prompt", rows=(reply(),))
        assert not panel.completion._values
        panel.handle_input(KeyEvent("tab"))
        panel.publish("complete_prompt", rows=(reply(value="wrong prefix"),))
        assert not panel.completion._values
        panel._arguments.set_text('{"topic":"changed elsewhere"}')
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        assert panel._mode == "arguments" and json.loads(panel._arguments.get_text()) == {"topic": "changed elsewhere"}
    finally:
        panel.close()


def test_template_completion_context_variables_and_scalar_expansion_require_separate_read():
    panel, sent = form("template")
    try:
        panel.handle_input(KeyEvent("tab"))
        panel.publish("complete_template", rows=(reply(kind="template", argument="name"),))
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        assert panel._arguments.get_text() == "fixture://completed%20value"
        assert sent[-1][0] == "complete_template"
        panel.handle_input(KeyEvent("space", ctrl=True))
        assert panel.completion.argument == "format"
        panel.handle_paste("js")
        panel.handle_input(KeyEvent("tab"))
        assert json.loads(sent[-1][3]["context"]) == {"name": "completed value"}
        panel.publish(
            "complete_template",
            rows=(reply(kind="template", argument="format", value="js", context=(("name", "completed value"),)),),
        )
        for budget in (0, 1, 3, 5, 8, 12):
            panel.set_render_budget(budget)
            rows = panel.render(32)
            assert len(rows) <= budget and all(visible_width(row) <= 32 and "\n" not in row for row in rows)
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        assert panel._arguments.get_text() == "fixture://completed%20value?format=completed%20value"
        panel.handle_input(KeyEvent("enter"))
        assert sent[-1] == ("read", "peer", "fixture://completed%20value?format=completed%20value", {})
    finally:
        panel.close()


def test_empty_or_invalid_argument_draft_is_preserved_and_cancel_never_submits():
    panel, sent = form()
    try:
        panel.handle_input(KeyEvent("escape"))
        assert panel._arguments.get_text() == "{}"
        panel._arguments.set_text("bad json")
        panel.handle_input(KeyEvent("enter"))
        assert panel._arguments.get_text() == "bad json"
        panel.handle_input(KeyEvent("space", ctrl=True))
        assert panel.completion is None and panel._arguments.get_text() == "bad json"
        assert sent[-1][0] == "prompts"
    finally:
        panel.close()


def test_control_text_in_suggestions_is_sanitized_and_not_multiline_terminal_output():
    panel, sent = form()
    try:
        panel.handle_input(KeyEvent("tab"))
        panel.publish("complete_prompt", rows=({**reply(), "values": ["\x1b[2Jbad\nvalue\tmore"]},))
        rows = panel.render(80)
        assert all("\n" not in row and "\x1b[2J" not in row for row in rows)
        panel.handle_input(KeyEvent("enter"))
        assert json.loads(panel._arguments.get_text()) == {"topic": "\x1b[2Jbad\nvalue\tmore"}
    finally:
        panel.close()


def test_variable_edits_used_as_context_are_retained_on_insert_and_hidden_budget_cannot_accept():
    panel, sent = form()
    try:
        panel.handle_paste("typed topic")
        panel.handle_input(KeyEvent("n", ctrl=True))
        panel.handle_paste("python")
        panel.handle_input(KeyEvent("tab"))
        response = {
            **reply(argument="language", value="python", context=(("topic", "typed topic"),)),
            "values": ("python3",),
        }
        panel.publish("complete_prompt", rows=(response,))
        panel.render(80)
        panel.set_render_budget(0)
        assert panel.render(80) == []
        panel.handle_input(KeyEvent("enter"))
        assert panel.completion is not None
        panel.set_render_budget(8)
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        assert json.loads(panel._arguments.get_text()) == {"topic": "typed topic", "language": "python3"}
        assert sent[-1][0] == "complete_prompt"
    finally:
        panel.close()


def test_blurred_completion_cannot_request_capture_edit_or_insert_and_regain_needs_repaint():
    panel, sent = form()
    try:
        panel.handle_input(KeyEvent('tab'))
        completion = panel.completion
        panel.focused = False
        assert not completion.focused and not completion._input.focused
        count = len(sent)
        panel.handle_paste('not the argument editor')
        panel.handle_input(KeyEvent('tab'))
        panel.handle_input(KeyEvent('enter'))
        assert len(sent) == count and completion._input.get_text() == '' and panel._mode == 'completion'
        # Original scoped response may be retained as inert data while background.
        panel.publish('complete_prompt', rows=(reply(),))
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        assert not completion._visible and panel._mode == 'completion' and panel._arguments.get_text() == '{}'
        panel.focused = True
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'completion'  # New focus has no painted insertion proof yet.
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'arguments' and json.loads(panel._arguments.get_text()) == {'topic': 'completed value'}
        assert len(sent) == count and panel._arguments.focused
    finally:
        panel.close()
