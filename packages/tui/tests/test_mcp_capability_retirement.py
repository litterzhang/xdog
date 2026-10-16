"""Retired reverse capabilities cannot take focus, submit input or start operations."""
from __future__ import annotations

import importlib

import pytest
from xdog.tui.components.mcp_panel import McpPanel
from xdog.tui.keys import KeyEvent, KeyEventType


@pytest.mark.parametrize('module', ['mcp_elicitation_panel', 'mcp_roots_panel', 'mcp_sampling_panel'])
def test_retired_capability_panel_is_not_importable(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module('xdog.tui.components.' + module)


@pytest.mark.parametrize('event_type', [KeyEventType.PRESS, KeyEventType.REPEAT, KeyEventType.RELEASE])
def test_retired_shortcuts_cannot_emit_requests_or_change_active_panel(event_type):
    sent, inserted = [], []
    panel = McpPanel(lambda *args: sent.append(args), lambda: None, lambda: inserted.append(True), lambda: None)
    try:
        panel.publish('list', rows=({'server': 'owned', 'state': 'disconnected'},))
        before = list(sent)
        for key in ('b', 'j', 'f', 'k', 'o'):
            panel.handle_input(KeyEvent(key, event_type=event_type))
        for key in ('b', 'f'):
            panel.handle_input(KeyEvent(key, alt=True, event_type=event_type))
        assert sent == before and not inserted and not panel._busy and panel._mode == 'servers'
        for name in ('sampling_review', 'elicitation_review', 'roots_review', 'publish_elicitation',
                     'publish_sampling', 'publish_roots', '_answer_elicitation', '_cancel_elicitation'):
            assert not hasattr(panel, name)
    finally:
        panel.close()
