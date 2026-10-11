"""Actual two-host alternate model selection without changing parent/provider identity."""
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

from packages.agent.tests.test_mcp_sampling_executor import CatalogAdapter
from packages.agent.tests.test_mcp_sampling_registration import config as peer_config
from packages.claw.tests.test_mcp_sampling_gateway import setup
from packages.coding.tests.test_mcp_sampling_ui import paint


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('withdraw', [False, True])
async def test_actual_model_catalog_alternate_sampling_or_withdrawn_selection_no_parent_switch(
    tmp_path, kind, withdraw,
):
    adapter = CatalogAdapter()
    server = task = writer = None
    if kind == 'coding':
        configs, log = peer_config(tmp_path)
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'mcp_servers': configs}))
        sessions = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='explicit-model')),
            session_data=sessions.create_session(model='explicit-model', working_dir=tmp_path),
            session_manager=sessions,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
        manager = session.mcp
    else:
        from xdog.claw.channels.tui.tui_app import ChatApp
        server, config, runtime, session, _, log = await setup(tmp_path, adapter)
        manager = runtime.mcp
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(5):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready and ui._mcp_sampling_supported)
        original_history = session.agent.messages
        ui._editor.set_text('PARENT CATALOG DRAFT')
        ui._open_mcp()
        await wait(lambda: not ui._mcp_panel._busy)
        panel = ui._mcp_panel
        panel.handle_input(KeyEvent('b'))
        await wait(lambda: not panel._busy)
        assert manager._sampling
        if kind == 'coding':
            await session.manage_mcp('connect', 'owned')
        else:
            panel.handle_input(KeyEvent('c'))
        await wait(lambda: panel.sampling_review is not None)
        review = panel.sampling_review
        assert review._models == ('explicit-model', 'alternate-model')
        review.editor.set_text(json.dumps({'model': 'alternate-model', 'max_tokens': 10,
                                          'context': 'none', 'tools': []}))
        review.handle_input(KeyEvent('enter'))
        paint(review)
        if withdraw:
            adapter.catalog = (adapter.catalog[0],)
        review.handle_input(KeyEvent('enter'))
        if withdraw:
            await wait(lambda: not manager._clients['owned'].reverse._jobs)
            assert not adapter.calls
        else:
            await wait(lambda: panel.sampling_review is not None and panel.sampling_review.event['phase'] == 'output')
            output = panel.sampling_review
            assert output.event['body']['model'] == 'alternate-model'
            assert len(adapter.calls) == 1 and adapter.calls[0][0] == 'alternate-model'
            paint(output)
            output.handle_input(KeyEvent('enter'))
            await wait(lambda: not manager._clients['owned'].reverse._jobs)
            await manager._bridge(manager._clients['owned'].request('ping', {}))
            rows = [json.loads(line) for line in log.read_text().splitlines()]
            reply = next(row for row in rows if row.get('id') == 'sample')
            assert reply['result']['model'] == 'alternate-model'
        assert session.agent.state.model == 'explicit-model'
        assert session.agent.messages is original_history
        assert ui._editor.get_text() == 'PARENT CATALOG DRAFT'
    finally:
        ui._request_exit()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
        if server is not None:
            await server.stop()
        else:
            await manager.aclose()
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            session.dispose()
