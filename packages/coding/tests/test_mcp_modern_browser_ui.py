"""UI-048: pinned modern SDK continuations inside actual native browser jobs."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent

from packages.agent.tests.test_mcp_sampling_executor import Adapter

SDK_ROOT = os.environ.get('XDOG_MCP_OFFICIAL_SDK_ROOT')
NODE = shutil.which('node')
PEER = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_official_sdk/input_peer.mjs'
pytestmark = pytest.mark.skipif(not SDK_ROOT or not NODE, reason='Explicit pinned independent SDK 2.3.1 required')


def records(log):
    return [json.loads(row) for row in log.read_text().splitlines()] if log.exists() else []


async def wait(ui, predicate, log):
    async with asyncio.timeout(8):
        while True:
            ui._poll()
            if predicate():
                return
            await asyncio.sleep(.003)


def paint(review):
    for _ in range(400):
        review.render(80)
        visible = review._review._visible if hasattr(review, '_review') else review._visible
        if visible:
            return
        review.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Exact review did not fully paint')


@asynccontextmanager
async def browser(tmp_path, host, transport):
    log = tmp_path / 'modern-peer.jsonl'
    process = session = gateway = task = writer = ui = None
    try:
        if transport == 'http':
            process = await asyncio.create_subprocess_exec(NODE, str(PEER), 'http', str(log),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            async with asyncio.timeout(5):
                port = json.loads(await process.stdout.readline())['port']
            config = {'transport': 'http', 'url': f'http://127.0.0.1:{port}/mcp', 'timeout': 20}
        else:
            config = {'command': NODE, 'args': [str(PEER), 'stdio', str(log)], 'timeout': 20}
        config['roots'] = [{'uri': 'file:///explicit', 'name': 'Explicit'}]
        configs = {'modern': config}
        adapter = Adapter()
        if host == 'coding':
            settings = tmp_path / 'settings.json'
            settings.write_text(json.dumps({'mcp_servers': configs}))
            store = SessionManager(tmp_path / 'sessions')
            session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='explicit-model')),
                session_data=store.create_session(model='explicit-model', working_dir=tmp_path), session_manager=store,
                settings=SettingsManager(global_path=settings), working_dir=tmp_path,
                tool_registry=None, bash=None, permissions=PermissionManager('deny'))
            manager = session.mcp
            ui = InteractiveMode(session)
            agent = session.agent
        else:
            config = ClawConfig(model='explicit-model', data_dir=str(tmp_path / 'data'),
                socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
                groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),), mcp_servers=configs)
            gateway = GatewayServer(config)
            with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                       return_value=('explicit-model', adapter)):
                await gateway.start()
            runtime = gateway._orchestrator._runtimes['main']
            agent = runtime.get_or_create_session(allow_reset=False).agent
            manager = runtime.mcp
            ui = ChatApp(config.socket_path)
            reader, writer = await asyncio.open_unix_connection(config.socket_path)
            task = asyncio.create_task(ui._run_connection(reader, writer))
            await wait(ui, lambda: ui._connection_ready and ui._mcp_sampling_supported
                       and ui._mcp_elicitation_supported, log)
        yield ui, manager, adapter, agent, log
    finally:
        if ui is not None:
            ui._request_exit()
        if session is not None:
            await session.mcp.aclose()
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            session.dispose()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        if gateway is not None:
            await gateway.stop()
        if process is not None:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 5)


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('transport', ['stdio', 'http'])
@pytest.mark.parametrize('method', ['resources/read', 'prompts/get'])
@pytest.mark.parametrize('family,late_closed', [('roots', False), ('sampling', False), ('form', False),
                                               ('url', False), ('sampling', True), ('form', True), ('url', True)])
async def test_modern_native_browser_armed_reviews_resume_outer_job_and_retain_final_document(
    tmp_path, host, transport, method, family, late_closed,
):
    async with browser(tmp_path, host, transport) as (ui, manager, adapter, agent, log):
        ui._editor.set_text('PARENT UNSENT MODERN')
        ui._open_mcp()
        await wait(ui, lambda: ui._mcp_panel is not None and not ui._mcp_panel._busy, log)
        panel = ui._mcp_panel
        events, closed, deferred = [], [], []
        for kind in ('sampling', 'elicitation'):
            publish = getattr(panel, 'publish_' + kind)
            close = getattr(panel, kind + '_closed')
            def tracked(event, *args, original=publish):
                events.append(event)
                return original(event, *args)
            def ended(identifier, original=close):
                closed.append(identifier)
                if late_closed:
                    deferred.append((original, identifier))
                    return None
                return original(identifier)
            setattr(panel, 'publish_' + kind, tracked)
            setattr(panel, kind + '_closed', ended)
        for key in ('b', 'f'):
            panel.handle_input(KeyEvent(key))
            await wait(ui, lambda: not panel._busy, log)
        assert manager._sampling and manager._elicitation and not records(log) and not adapter.calls
        panel.handle_input(KeyEvent('c'))
        await wait(ui, lambda: not panel._busy and manager.status()[0]['state'] == 'connected', log)
        assert manager._clients['modern'].modern
        assert manager.status()[0]['client_capabilities'] == {
            'roots': {}, 'sampling': {'context': {}, 'tools': {}}, 'elicitation': {'form': {}, 'url': {}}}
        panel.handle_input(KeyEvent('u' if method == 'resources/read' else 'p'))
        await wait(ui, lambda: not panel._busy and panel._mode == 'catalog', log)
        name = 'test://' + family if method == 'resources/read' else family
        panel._selected = next(index for index, row in enumerate(panel._visible()) if row['name'] == name)
        panel.handle_input(KeyEvent('enter'))
        if method == 'prompts/get':
            assert panel._mode == 'arguments' and panel._arguments.get_text() == '{}'
            panel.handle_input(KeyEvent('enter'))
        original_job = ui._mcp_active
        if family != 'roots':
            await wait(ui, lambda: panel.sampling_review is not None or panel.elicitation_review is not None
                       or not panel._busy, log)
            review = panel.sampling_review if family == 'sampling' else panel.elicitation_review
            assert review is not None, (panel._message, records(log))
            assert review.event['protocol'] == '2026-07-28'
            assert ui._mcp_active == original_job and ui._mcp_document is None
            assert len([row for row in records(log) if row['type'] == 'operation']) == 1
            assert not adapter.calls
            if family == 'sampling':
                review.editor.set_text(json.dumps({'model': 'explicit-model', 'max_tokens': 10,
                                                  'context': 'none', 'tools': []}))
                review.handle_input(KeyEvent('enter'))
                review.handle_input(KeyEvent('enter'))
                assert not adapter.calls
                paint(review)
                review.handle_input(KeyEvent('enter'))
            else:
                if family == 'url':
                    assert 'elicitationId' not in review.event['body']['params']
                review.handle_input(KeyEvent('enter'))
                assert review._mode == 'request'
                paint(review)
                review.handle_input(KeyEvent('enter'))
                if family == 'form':
                    assert review._mode == 'form'
                    review.editor.set_text('{"name":"MODERN REVIEWED FORM"}')
                    review.handle_input(KeyEvent('enter'))
                    paint(review)
                    review.handle_input(KeyEvent('enter'))
            phase = 'output' if family == 'sampling' else 'response'
            await wait(ui, lambda: (panel.sampling_review if family == 'sampling' else panel.elicitation_review)
                       is not None, log)
            response = panel.sampling_review if family == 'sampling' else panel.elicitation_review
            assert response.event['phase'] == phase
            assert ui._mcp_active == original_job and ui._mcp_document is None
            assert len([row for row in records(log) if row['type'] == 'operation']) == 1
            response.handle_input(KeyEvent('enter'))
            assert not closed
            paint(response)
            response.handle_input(KeyEvent('enter'))
        await wait(ui, lambda: ui._mcp_document is not None and not panel._busy, log)
        assert 'APPROVED-' + family in ui._mcp_document.text
        if family != 'roots':
            await wait(ui, lambda: bool(closed), log)
        assert panel._mode == 'preview' and panel._preview is not None
        retained_preview = panel._preview
        for callback, identifier in deferred:
            callback(identifier)  # Authentic admitted close, deliberately delivered after the outer result.
        assert panel._preview is retained_preview and panel._mode == 'preview'
        await wait(ui, lambda: 'APPROVED-' + family in '\n'.join(panel.render(80)), log)
        assert ui._editor.get_text() == 'PARENT UNSENT MODERN' and not agent.messages
        assert len(adapter.calls) == (1 if family == 'sampling' else 0)
        assert not manager._clients['modern'].inputs._jobs
        assert not manager._elicitation['modern'][1]._urls
        requests = [row['message'] for row in records(log) if row['type'] == 'inbound'
                    and row['message'].get('method') == method]
        assert len(requests) == 2 and requests[0]['id'] != requests[1]['id']
        assert requests[1]['params']['requestState'] == '  opaque\x00state/汉字\n  '
        assert set(requests[1]['params']['inputResponses']) == {'review'}
        assert not any(row['type'] == 'inbound' and row['message'].get('method') == 'tools/call'
                       for row in records(log))
        assert all(event['protocol'] == '2026-07-28' for event in events)
