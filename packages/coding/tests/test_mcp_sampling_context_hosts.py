"""Both actual hosts capture scoped MCP context then require independent disclosure approval."""
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

from packages.agent.tests.test_mcp_sampling_context import turn
from packages.agent.tests.test_mcp_sampling_executor import Adapter
from packages.agent.tests.test_mcp_sampling_registration import config as peer_config
from packages.claw.tests.test_mcp_sampling_gateway import setup
from packages.coding.tests.test_mcp_sampling_ui import paint


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('approve_context', [False, True])
async def test_actual_host_scoped_context_review_blocks_model_and_freezes_snapshot(tmp_path, kind, approve_context):
    server = task = writer = None
    adapter = Adapter()
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
        session._restore_messages()
        manager = runtime.mcp
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    peer = tmp_path / 'peer.py'
    peer.write_text(peer.read_text().replace("'maxTokens':20", "'maxTokens':20,'includeContext':'thisServer'")
        .replace("'tools':[]", "'tools':[{'name':'lookup','inputSchema':{'type':'object'}}]"))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready and ui._mcp_sampling_supported)
        ui._editor.set_text('UNSENT PARENT CONTEXT DRAFT')
        ui._open_mcp()
        await wait(lambda: not ui._mcp_panel._busy)
        panel = ui._mcp_panel
        panel.handle_input(KeyEvent('b'))
        await wait(lambda: not panel._busy)
        if kind == 'coding':
            await session.manage_mcp('connect', 'owned')
        else:
            panel.handle_input(KeyEvent('c'))
        await wait(lambda: panel.sampling_review is not None)
        await wait(lambda: bool(manager.tools()))  # Sampling can arrive before catalog publication finishes.
        alias = manager.tools()[0].name
        from dataclasses import replace
        related_turn = list(turn(alias, 'original-call', 'PUBLIC RELATED'))
        structured = {'answer': 'PUBLIC STRUCTURED RESULT', 'api_key': 'PRIVATE STRUCTURED KEY'}
        related_turn[2] = replace(related_turn[2], details={'mcp_server': 'owned',
            'structured_content_present': True, 'structured_content': structured,
            'host_private': 'PRIVATE HOST DETAIL'})
        session.agent.replace_messages([*related_turn,
                                        *turn(alias, 'original-call', 'SECOND RELATED REUSED ID'),
                                        *turn('local-only', 'local-call', 'UNRELATED PRIVATE')])
        history = session.agent.messages
        review = panel.sampling_review
        review.editor.set_text(json.dumps({'model': 'explicit-model', 'max_tokens': 10,
                                          'context': 'thisServer', 'tools': []}))
        review.handle_input(KeyEvent('enter'))
        paint(review)
        review.handle_input(KeyEvent('enter'))
        await wait(lambda: panel.sampling_review is not None and panel.sampling_review.event['phase'] == 'context')
        disclosure = panel.sampling_review
        text = str(disclosure.event['body'])
        assert 'PUBLIC RELATED' in text and 'UNRELATED PRIVATE' not in text
        assert 'PRIVATE KEY' not in text and 'PRIVATE REASONING' not in text and 'PRIVATE SIGNATURE' not in text
        assert 'SECOND RELATED REUSED ID' in text
        assert 'PUBLIC STRUCTURED RESULT' in text and 'PRIVATE STRUCTURED KEY' not in text
        assert 'PRIVATE HOST DETAIL' not in text
        structured['answer'] = 'LATER UNREVIEWED STRUCTURED'
        assert not adapter.calls
        disclosure.handle_input(KeyEvent('enter'))
        assert not adapter.calls  # Unpainted context approval cannot execute.
        if approve_context:
            paint(disclosure)
            disclosure.handle_input(KeyEvent('enter'))
            await wait(lambda: panel.sampling_review is not None and panel.sampling_review.event['phase'] == 'output')
            assert len(adapter.calls) == 1
            model_history = str(adapter.calls[0][1].messages)
            assert 'PUBLIC RELATED' in model_history and 'UNRELATED PRIVATE' not in model_history
            assert 'PRIVATE KEY' not in model_history
            assert 'SECOND RELATED REUSED ID' in model_history
            assert 'PUBLIC STRUCTURED RESULT' in model_history
            assert 'LATER UNREVIEWED STRUCTURED' not in model_history
            assert 'PRIVATE STRUCTURED KEY' not in model_history
            output = panel.sampling_review
            paint(output)
            output.handle_input(KeyEvent('enter'))
            await wait(lambda: not manager._clients['owned'].reverse._jobs)
        else:
            disclosure.handle_input(KeyEvent('escape'))
            await wait(lambda: not manager._sampling)
            assert not adapter.calls
        assert session.agent.messages is history
        assert ui._editor.get_text() == 'UNSENT PARENT CONTEXT DRAFT'
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
