"""Manual completion is absent; explicit prompt input remains editable and inert."""
from __future__ import annotations

import importlib.util
import json

import pytest
from xdog.tui.components.mcp_panel import McpPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.utils import visible_width


@pytest.fixture
def prompt_form():
    sent, inserted, exported = [], [], []
    panel = McpPanel(lambda *args: sent.append(args), lambda: None,
                     lambda: inserted.append(True), lambda: exported.append(True))
    panel.focused = True
    panel.publish("list", rows=({"server": "peer", "state": "connected"},))
    panel.handle_input(KeyEvent("p"))
    panel.publish("prompts", rows=({"name": "review", "arguments": (("topic", "", True),)},))
    panel.handle_input(KeyEvent("enter"))
    try:
        yield panel, sent, inserted, exported
    finally:
        panel.close()


def test_dedicated_picker_and_parent_completion_api_are_removed(prompt_form):
    panel, sent, _, _ = prompt_form
    assert importlib.util.find_spec("xdog.tui.components.mcp_completion") is None
    for attribute in ("completion", "_completion_names", "_completion_origin", "_open_completion",
                      "_insert_completion", "_cancel_completion", "_close_completion"):
        assert not hasattr(panel, attribute)
    panel._arguments.set_text('{"topic":"literal quoted \\\" value 中文"}')
    draft = panel._arguments.get_text()
    count = len(sent)
    for event in (KeyEvent("space", ctrl=True), KeyEvent("tab"), KeyEvent("n", ctrl=True),
                  KeyEvent("p", ctrl=True)):
        panel.handle_input(event)
        assert panel._mode == "arguments" and panel._arguments.get_text() == draft
    assert len(sent) == count
    assert "Ctrl+Space" not in "\n".join(panel.render(120))
    panel.handle_input(KeyEvent("enter"))
    assert sent[-1] == ("prompt", "peer", "review", json.loads(draft))


@pytest.mark.parametrize("draft", ["bad json", "[]", "null", '{"topic":false}', '{"topic":1}',
                                   '{"topic":[]}', '{"topic":{}}'])
def test_invalid_argument_input_preserved_without_request_or_model_insertion(prompt_form, draft):
    panel, sent, inserted, exported = prompt_form
    count = len(sent)
    panel._arguments.set_text(draft)
    panel.handle_input(KeyEvent("enter"))
    assert panel._arguments.get_text() == draft and panel._mode == "arguments"
    assert len(sent) == count and not inserted and not exported
    assert panel._message


def test_literal_json_values_survive_without_completion_or_implicit_execution(prompt_form):
    panel, sent, inserted, exported = prompt_form
    arguments = {"topic": 'quotes " /path/../../file \\ newline\n中文\x1b[2J', "optional": ""}
    panel._arguments.set_text("")
    panel.handle_paste(json.dumps(arguments, ensure_ascii=False))
    assert sent[-1][0] == "prompts"
    panel.handle_input(KeyEvent("enter", event_type=KeyEventType.REPEAT))
    panel.handle_input(KeyEvent("enter", event_type=KeyEventType.RELEASE))
    assert sent[-1][0] == "prompts" and not inserted and not exported
    panel.handle_input(KeyEvent("enter"))
    assert sent[-1] == ("prompt", "peer", "review", arguments)
    count = len(sent)
    panel.handle_input(KeyEvent("enter"))
    assert len(sent) == count  # In-flight requests are not repeated.


@pytest.mark.parametrize("action", ["complete_prompt", "complete_template", "complete_resource",
                                    "resource_completion_preview"])
def test_retired_picker_reply_cannot_replace_draft_or_unlock_new_request(prompt_form, action):
    panel, sent, _, _ = prompt_form
    panel._arguments.set_text('{"topic":"new request"}')
    panel.handle_input(KeyEvent("enter"))
    count = len(sent)
    panel.publish(action, rows=({"values": ("stale",)},), text="stale untrusted preview", error="late error")
    assert panel._busy and panel._mode == "arguments" and panel._preview is None
    assert panel._arguments.get_text() == '{"topic":"new request"}'
    panel.handle_input(KeyEvent("enter"))
    assert len(sent) == count


def test_error_timeout_remains_editable_and_cancel_does_not_submit(prompt_form):
    panel, sent, inserted, exported = prompt_form
    panel._arguments.set_text('{"topic":"retry manually"}')
    panel.handle_input(KeyEvent("enter"))
    panel.publish("prompt", error="MCP request timed out")
    assert not panel._busy and "timed out" in "\n".join(panel.render(120))
    assert panel._arguments.get_text() == '{"topic":"retry manually"}'
    count = len(sent)
    panel.handle_input(KeyEvent("escape"))
    assert sent[count:] == [("list", "", "", {})]
    assert not inserted and not exported


def test_argument_editor_remains_bounded_without_rendering_literal_controls(prompt_form):
    panel, sent, _, _ = prompt_form
    panel._arguments.set_text(json.dumps({"topic": "USER-INPUT 中文 " * 12 + "\x1b[2J\n"}, ensure_ascii=False))
    count = len(sent)
    for height in (0, 1, 3, 5, 8, 12):
        panel.set_render_budget(height)
        for width in (24, 32, 80):
            rows = panel.render(width)
            assert len(rows) <= height
            assert all(visible_width(row) <= width and "\n" not in row and "\x1b[2J" not in row for row in rows)
    assert len(sent) == count
