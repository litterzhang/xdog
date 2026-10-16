"""Watch gestures are inert; retained native browse requires explicit requests."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from xdog.agent.mcp.context import McpEntry
from xdog.tui.keys import KeyEvent, KeyEventType

from packages.coding.tests.test_mcp_manual_picker_ui import native_mcp as native_mcp


def test_retired_watch_keys_never_wire_or_arm_an_editor(native_mcp):
    host, ui, panel, sent, session, calls = native_mcp
    ui._editor.set_text('PRIMARY DRAFT /quit')
    before = list(sent)
    for event_type in KeyEventType:
        for event in (KeyEvent('w', event_type=event_type), KeyEvent('w', alt=True, event_type=event_type),
                      KeyEvent('t', alt=True, event_type=event_type),
                      KeyEvent('p', alt=True, event_type=event_type),
                      KeyEvent('u', alt=True, event_type=event_type)):
            panel.handle_input(event)
    panel.handle_paste('{"toolsListChanged":true}')
    assert sent == before and panel._mode == 'servers' and not panel._busy
    assert not hasattr(panel, 'publish_watch') and not hasattr(panel, '_subscription_form')
    assert ui._editor.get_text() == 'PRIMARY DRAFT /quit' and not calls
    assert 'watch' not in '\n'.join(panel.render(160)).lower()
    if host == 'coding':
        from packages.coding.tests.test_mcp_browser import wait_ui
        with patch.object(session.mcp, 'catalog', AsyncMock(return_value=(
                McpEntry('fixture', 'resource', 'test://manual', 'Manual'),))) as catalog:
            panel.handle_input(KeyEvent('u'))
            wait_ui(ui, lambda: not panel._busy)
            catalog.assert_awaited_once_with('fixture', 'resource')
        assert panel._mode == 'catalog'
    else:
        panel.handle_input(KeyEvent('u'))
        assert sent[-1]['type'] == 'mcp_context' and sent[-1]['action'] == 'resources'
        assert not any('watch' in key for request in sent for key in request)
