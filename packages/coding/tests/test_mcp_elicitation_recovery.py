"""Real two-host constrained form recovery rotates receipt without releasing invalid data."""
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

from packages.agent.tests.test_mcp_elicitation_registration import peer_config
from packages.agent.tests.test_mcp_sampling_executor import Adapter
from packages.claw.tests.test_mcp_elicitation_gateway import setup
from packages.coding.tests.test_mcp_elicitation_ui import paint


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_constrained_invalid_reply_reopens_original_request_and_keeps_editable_draft(tmp_path, kind):
    server = task = writer = None
    if kind == 'coding':
        configs, log = peer_config(tmp_path)
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'mcp_servers': configs}))
        sessions, adapter = SessionManager(tmp_path / 'sessions'), Adapter()
        session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='explicit-model')),
            session_data=sessions.create_session(model='explicit-model', working_dir=tmp_path),
            session_manager=sessions, settings=SettingsManager(global_path=settings), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
        manager = session.mcp
    else:
        from xdog.claw.channels.tui.tui_app import ChatApp
        server, config, runtime, session, adapter, log = await setup(tmp_path)
        manager = runtime.mcp
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    peer = tmp_path / 'peer.py'
    peer.write_text(peer.read_text().replace("'name':{'type':'string'}", "'name':{'type':'string','minLength':5}"))
    async def wait(predicate):
        async with asyncio.timeout(5):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready and ui._mcp_elicitation_supported)
        ui._editor.set_text('PARENT RECOVERY DRAFT')
        ui._open_mcp()
        await wait(lambda: not ui._mcp_panel._busy)
        panel = ui._mcp_panel
        panel.handle_input(KeyEvent('f'))
        await wait(lambda: not panel._busy)
        if kind == 'coding':
            await session.manage_mcp('connect', 'owned')
        else:
            panel.handle_input(KeyEvent('c'))
        await wait(lambda: panel.elicitation_review is not None)
        original = panel.elicitation_review
        old = original.event
        paint(original)
        original.handle_input(KeyEvent('enter'))
        original.editor.set_text('{"name":"a"}')
        original.handle_input(KeyEvent('enter'))
        paint(original)
        original.handle_input(KeyEvent('enter'))
        await wait(lambda: panel.elicitation_review is not None and panel.elicitation_review is not original)
        recovered = panel.elicitation_review
        new = recovered.event
        assert new['id'] == old['id'] and new['receipt'] != old['receipt'] and new['sha256'] == old['sha256']
        assert new['phase'] == 'request' and new['form_draft'] == {'name': 'a'}
        assert json.loads(recovered.editor.get_text()) == {'name': 'a'}
        assert manager._elicitation and manager._clients['owned'].reverse._jobs
        assert not any(json.loads(row).get('id') == 'form' for row in log.read_text().splitlines())
        recovered.handle_input(KeyEvent('enter'))
        assert recovered._mode == 'request'  # Fresh review needs fresh painting.
        paint(recovered)
        recovered.handle_input(KeyEvent('enter'))
        assert recovered._mode == 'form'
        recovered.editor.set_text('{"name":"CORRECTED VALID"}')
        recovered.handle_input(KeyEvent('enter'))
        paint(recovered)
        recovered.handle_input(KeyEvent('enter'))
        await wait(lambda: panel.elicitation_review is not None
                   and panel.elicitation_review.event['phase'] == 'response')
        final = panel.elicitation_review
        assert final.event['body']['response']['content'] == {'name': 'CORRECTED VALID'}
        paint(final)
        final.handle_input(KeyEvent('enter'))
        await wait(lambda: not manager._clients['owned'].reverse._jobs)
        await manager._bridge(manager._clients['owned'].request('ping', {}))
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        released = [row for row in rows if row.get('id') == 'form']
        assert len(released) == 1 and released[0]['result']['content'] == {'name': 'CORRECTED VALID'}
        assert not adapter.calls and not session.agent.messages
        assert ui._editor.get_text() == 'PARENT RECOVERY DRAFT'
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
