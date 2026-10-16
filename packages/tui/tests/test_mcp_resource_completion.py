"""No opaque completion form; resources still require explicit selection/read."""
from __future__ import annotations

import importlib.util

import pytest
from xdog.tui.components.mcp_panel import McpPanel
from xdog.tui.keys import KeyEvent, KeyEventType


@pytest.fixture
def resource_panel():
    sent, inserted, exported = [], [], []
    panel = McpPanel(lambda *args: sent.append(args), lambda: None,
                     lambda: inserted.append(True), lambda: exported.append(True))
    panel.focused = True
    panel.publish("list", rows=({"server": "peer", "state": "connected"},))
    try:
        yield panel, sent, inserted, exported
    finally:
        panel.close()


@pytest.mark.parametrize("catalog", [False, True])
def test_opaque_resource_form_and_variable_chooser_are_absent(resource_panel, catalog):
    panel, sent, _, _ = resource_panel
    if catalog:
        panel.handle_input(KeyEvent("u"))
        panel.publish("resources", rows=({"name": "opaque://known/ref", "title": "Resource"},))
    mode, count = panel._mode, len(sent)
    panel.handle_input(KeyEvent("v"))
    panel.handle_input(KeyEvent("space", ctrl=True))
    assert panel._mode == mode and len(sent) == count
    assert not hasattr(panel, "_resource_form") and not hasattr(panel, "completion")
    assert importlib.util.find_spec("xdog.tui.mcp_resource") is None
    assert "completion" not in "\n".join(panel.render(120)).lower()


def test_catalog_selection_reads_only_on_distinct_press_and_never_inserts(resource_panel):
    panel, sent, inserted, exported = resource_panel
    panel.handle_input(KeyEvent("u"))
    panel.publish("resources", rows=({"name": "opaque://known/ref", "title": "Resource"},))
    count = len(sent)
    for event_type in (KeyEventType.RELEASE, KeyEventType.REPEAT):
        panel.handle_input(KeyEvent("enter", event_type=event_type))
    assert len(sent) == count
    panel.handle_input(KeyEvent("enter"))
    assert sent[-1] == ("read", "peer", "opaque://known/ref", {})
    assert not inserted and not exported


@pytest.mark.parametrize("draft", ["", "  ", "opaque://{unresolved}/ref", "opaque://unbalanced}", "x" * 8193])
def test_invalid_explicit_uri_preserves_input_without_resource_read(resource_panel, draft):
    panel, sent, inserted, exported = resource_panel
    panel.handle_input(KeyEvent("t"))
    panel.publish("templates", rows=({"name": "opaque://{name}/ref", "arguments": (("name", "", False),)},))
    panel.handle_input(KeyEvent("enter"))
    count = len(sent)
    panel._arguments.set_text(draft)
    panel.handle_input(KeyEvent("enter"))
    assert panel._arguments.get_text() == draft and panel._mode == "uri"
    assert len(sent) == count and not inserted and not exported


def test_template_input_is_literal_without_expansion_and_requires_separate_read(resource_panel):
    panel, sent, inserted, exported = resource_panel
    assert importlib.util.find_spec("xdog.tui.scalar_template") is None
    panel.handle_input(KeyEvent("t"))
    template = "opaque://{name}{?format}"
    panel.publish("templates", rows=({"name": template, "arguments": (("name", "", False),)},))
    panel.handle_input(KeyEvent("enter"))
    count = len(sent)
    for event in (KeyEvent("space", ctrl=True), KeyEvent("tab"), KeyEvent("n", ctrl=True)):
        panel.handle_input(event)
    assert panel._arguments.get_text() == template and panel._mode == "uri" and len(sent) == count
    panel._arguments.set_text("")
    panel.handle_paste("opaque://explicit/中文?format=literal%20value")
    assert len(sent) == count
    panel.handle_input(KeyEvent("enter"))
    assert sent[-1] == ("read", "peer", "opaque://explicit/中文?format=literal%20value", {})
    assert not inserted and not exported
    panel.publish("read", error="Resource request canceled")
    assert not panel._busy and panel._arguments.get_text() == "opaque://explicit/中文?format=literal%20value"
    count = len(sent)
    panel.handle_input(KeyEvent("escape"))
    assert sent[count:] == [("list", "", "", {})]  # No automatic retry/read on cancel.
