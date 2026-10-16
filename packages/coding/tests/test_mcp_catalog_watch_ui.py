"""Retired active watches cannot cancel a newer native MCP operation."""
from __future__ import annotations

import threading

import pytest
from xdog.agent.mcp.context import McpBlock, McpDocument

from packages.coding.tests.test_mcp_manual_picker_ui import native_mcp as native_mcp


@pytest.mark.parametrize('action', ['watch_resource', 'unwatch_resource', 'watch_catalog', 'unwatch_catalog',
                                    'watch_notifications', 'unwatch_notifications'])
def test_retired_watch_request_and_result_do_not_mutate_successor(native_mcp, action):
    host, ui, panel, sent, session, calls = native_mcp
    original = McpDocument('fixture', 'resource', 'original', (McpBlock('text', 'retained snapshot'),))
    ui._mcp_document = original
    ui._mcp_active, ui._mcp_action = 'successor', 'read'
    panel._busy, panel._message = True, 'successor read pending'
    before = list(sent)
    sequence = ui._mcp_sequence
    if host == 'coding':
        ui._mcp_cancel = threading.Event()
    ui._mcp_send(action, 'fixture', 'test://old', {})
    ui._mcp_publish(action, (), 'retired result')
    assert ui._mcp_active == 'successor' and ui._mcp_action == 'read'
    assert panel._busy and panel._message == 'successor read pending'
    assert ui._mcp_document is original and ui._mcp_sequence == sequence
    assert sent == before and not calls
    assert not any('watch' in name for name in vars(ui) if name.startswith('_mcp_'))
    if session is not None:
        assert not ui._mcp_cancel.is_set() and not session.messages
