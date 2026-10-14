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
