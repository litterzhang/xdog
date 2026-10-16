"""Configured Roots remain SDK metadata; mutable native disclosure panels are gone."""
from __future__ import annotations

import importlib

import pytest
from xdog.agent.mcp import McpManager
from xdog.coding.core.agent_session import AgentSession
from xdog.tui.components.mcp_panel import McpPanel
from xdog.tui.keys import KeyEvent


def test_mutable_roots_review_modules_and_host_controls_are_absent():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module('xdog.tui.components.mcp_roots_panel')
    assert not hasattr(McpManager, 'manage_roots') and not hasattr(McpManager, 'detach_roots')
    assert not hasattr(AgentSession, 'manage_mcp_roots')
    sent = []
    panel = McpPanel(lambda *args: sent.append(args), lambda: None, lambda: None, lambda: None)
    try:
        panel.publish('list', rows=({'server': 'owned', 'state': 'connected'},))
        panel.set_render_budget(10)
        before = list(sent)
        panel.handle_input(KeyEvent('o'))
        assert sent == before and panel._mode == 'servers'
        assert not hasattr(panel, 'roots_review') and not hasattr(panel, 'publish_roots')
        assert 'roots review' not in '\n'.join(panel.render(120)).lower()
    finally:
        panel.close()
