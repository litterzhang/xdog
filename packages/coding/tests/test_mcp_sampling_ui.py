"""Actual Coding sampling opt-in, original view and fully painted dual review."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent

from packages.agent.tests.test_mcp_sampling_executor import Adapter
from packages.agent.tests.test_mcp_sampling_registration import config


async def wait(ui, predicate):
    async with asyncio.timeout(5):
        while True:
            ui._poll()
            if predicate():
                return
            await asyncio.sleep(.003)


def paint(panel):
    for _ in range(300):
        panel.render(80)
        if panel._visible:
            return
        panel.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Full sampling review did not paint')


@pytest.mark.parametrize('end', ['release', 'deny', 'close'])
async def test_actual_coding_sampling_two_reviews_no_parent_history_or_unapproved_peer_output(tmp_path, end):
    configs, log = config(tmp_path)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'mcp_servers': configs}))
    sessions = SessionManager(tmp_path / 'sessions')
    adapter = Adapter()
    session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='explicit-model')),
        session_data=sessions.create_session(model='explicit-model', working_dir=tmp_path), session_manager=sessions,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('deny'))
    ui = InteractiveMode(session)
    try:
        ui._editor.set_text('PARENT UNSENT')
        ui._open_mcp()
        await wait(ui, lambda: not ui._mcp_panel._busy)
        panel = ui._mcp_panel
        assert not session.mcp._sampling and not log.exists()
        panel.handle_input(KeyEvent('b'))
        await wait(ui, lambda: not panel._busy)
        assert session.mcp._sampling and not log.exists()
        await session.manage_mcp('connect', 'owned')
        await wait(ui, lambda: panel.sampling_review is not None)
        review = panel.sampling_review
        assert not adapter.calls and review.event['phase'] == 'input'
        review.editor.set_text(json.dumps({'model': 'explicit-model', 'max_tokens': 10,
                                          'context': 'none', 'tools': []}))
        review.handle_input(KeyEvent('enter'))
        review.handle_input(KeyEvent('enter'))
        assert not adapter.calls
        paint(review)
        review.handle_input(KeyEvent('enter'))
        await wait(ui, lambda: panel.sampling_review is not None and panel.sampling_review.event['phase'] == 'output')
        assert len(adapter.calls) == 1 and session.messages == []
        assert session.permissions.mode == 'deny' and ui._editor.get_text() == 'PARENT UNSENT'
        frames = [json.loads(row) for row in log.read_text().splitlines()]
        assert not any(row.get('id') == 'sample' for row in frames)
        output = panel.sampling_review
        if end == 'release':
            paint(output)
            output.handle_input(KeyEvent('enter'))
            await wait(ui, lambda: not session.mcp._clients['owned'].reverse._jobs)
            await session.mcp._bridge(session.mcp._clients['owned'].request('ping', {}))
            frames = [json.loads(row) for row in log.read_text().splitlines()]
            replies = [row for row in frames if row.get('id') == 'sample']
            assert len(replies) == 1 and replies[0]['result']['content']['text'] == 'MODEL OUTPUT'
        elif end == 'deny':
            output.handle_input(KeyEvent('escape'))
            await wait(ui, lambda: not session.mcp._sampling)
        else:
            ui._close_mcp()
            await wait(ui, lambda: not session.mcp._sampling)
        assert not session._auth_changing and len(adapter.calls) == 1
        assert ui._editor.get_text() == 'PARENT UNSENT'
    finally:
        ui._request_exit()
        await session.mcp.aclose()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        session.dispose()


async def test_sampling_enable_connected_server_refuses_without_crashing_or_provider_call(tmp_path):
    configs, log = config(tmp_path)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'mcp_servers': configs}))
    sessions, adapter = SessionManager(tmp_path / 'sessions'), Adapter()
    session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='explicit-model')),
        session_data=sessions.create_session(model='explicit-model', working_dir=tmp_path), session_manager=sessions,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('deny'))
    ui = InteractiveMode(session)
    try:
        await session.manage_mcp('connect', 'owned')
        ui._open_mcp()
        await wait(ui, lambda: not ui._mcp_panel._busy)
        ui._mcp_panel.handle_input(KeyEvent('b'))
        await wait(ui, lambda: not ui._mcp_panel._busy)
        assert 'disconnected' in ui._mcp_panel._message
        assert not ui._sampling_bindings and not session.mcp._sampling and not adapter.calls
    finally:
        ui._request_exit()
        await session.mcp.aclose()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        session.dispose()


async def test_active_sampling_review_cannot_be_replaced_by_another_request_closed_event(tmp_path):
    from xdog.tui.components.mcp_panel import McpPanel

    from packages.tui.tests.test_mcp_sampling_panel import event
    panel = McpPanel(lambda *args: None, lambda: None, lambda: None, lambda: None)
    panel.focused = True
    panel.publish_sampling(event(), ('host-model',))
    original = panel.sampling_review
    with pytest.raises(ValueError, match='focus'):
        panel.publish_sampling(event(), ('host-model',))
    panel.sampling_closed('other-request')
    assert panel.sampling_review is original
    assert panel.requires_visible_confirmation and panel.requires_current_viewport_input
    panel.close()
