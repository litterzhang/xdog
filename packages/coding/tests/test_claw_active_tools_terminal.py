"""Physical private Claw gateway/UI active tool review and native terminal recovery."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Owned Linux terminal required')


@pytest.mark.parametrize('approve', [False, True])
def test_physical_claw_active_tools_full_review_original_group_and_termios(tmp_path, approve):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/toolselector', root / 'toolselector')
    config = ClawConfig(model='offline', data_dir=str(tmp_path / 'data'), socket_path=str(tmp_path / 'socket'),
        pid_file=str(tmp_path / 'pid'), groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('toolselector',), extension_dir=str(root))
    ready, stop = threading.Event(), threading.Event()
    owned, errors, calls = {}, [], []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    def serve():
        async def run():
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline', stream)):
                    await server.start()
                runtime = server._orchestrator._runtimes['main']
                session = runtime.get_or_create_session(allow_reset=False)
                await session.initialize_extensions()
                owned['agent'] = session.agent
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(.01)
            except BaseException as exc:
                errors.append(exc)
                ready.set()
            finally:
                await server.stop()
        asyncio.run(run())
    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    terminal = Terminal(tmp_path)
    try:
        assert ready.wait(6) and not errors
        terminal.start('claw', config.socket_path)
        before = tuple(tool.name for tool in owned['agent'].active_tools)
        assert before
        terminal.text('/ext-toolselector-select ' + json.dumps({'names': []}))
        terminal.keys('Enter')
        terminal.wait('Queue this active-tool selection')
        if approve:
            terminal.keys('Right', 'Enter')
            terminal.wait('form review')
            terminal.resize(60, 18)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if 'Enter submits reviewed form' in terminal.visible():
                    break
                terminal.keys('NPage')
                time.sleep(.04)
            else:
                raise AssertionError(terminal.visible())
            assert tuple(tool.name for tool in owned['agent'].active_tools) == before
            terminal.keys('Enter')
            terminal.wait('Active tool selection applied')
            assert not owned['agent'].active_tools
            terminal.text('/ext-toolselector-inspect')
            terminal.keys('Enter')
            terminal.wait('ACTIVE-TOOL-COUNT-0')
            terminal.resize(100, 30)
            terminal.text('/tools')
            terminal.keys('Enter')
            terminal.wait('Selection: explicit')
            terminal.wait('selected 0 /')
            terminal.text('/')
            terminal.text('filesystem')
            terminal.keys('Enter')
            terminal.wait('Selection: excluded')
            terminal.keys('Escape')
            terminal.wait_hidden('Tools (inline)')

        else:
            terminal.keys('Escape')
            terminal.wait('TOOLS-QUEUED')
            assert tuple(tool.name for tool in owned['agent'].active_tools) == before
        assert not calls
        terminal.wait_cursor(True)
        terminal.text('UNSENT PARENT AFTER SELECTION')
        terminal.wait_editor('UNSENT PARENT AFTER SELECTION')
        terminal.finish()
    finally:
        terminal.call('kill-server')
        stop.set()
        worker.join(6)
        assert not worker.is_alive() and not errors
