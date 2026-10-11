"""Cold Linux Claw /login exact consent/masked code; no browser/model/service action."""
from __future__ import annotations

import asyncio
import shutil
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.agent.tests.test_extension_provider_oauth import ACCESS, CODE, PROVIDER, REFRESH
from packages.coding.tests.test_provider_oauth_terminal import reviewed
from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Owned current-Linux terminal required')


@pytest.mark.parametrize('cancel', [False, True])
def test_physical_cold_claw_provider_login_menu_and_masked_cancel_restore_terminal(tmp_path, cancel):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/provideroauth', root / 'provideroauth')
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('provideroauth',), extension_dir=str(root))
    ready, stop = threading.Event(), threading.Event()
    calls, errors, owned = [], [], {}
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    def serve():
        async def run():
            server = GatewayServer(config)
            try:
                with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                           return_value=('offline/model', stream)):
                    await server.start()
                owned['runtime'] = server._orchestrator._runtimes['main']
                assert owned['runtime']._active_session is None
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(.01)
            except BaseException as exc:
                errors.append(exc)
                ready.set()
            finally:
                await asyncio.wait_for(server.stop(), 10)
        asyncio.run(run())
    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    assert ready.wait(6) and not errors
    terminal = Terminal(tmp_path)
    try:
        terminal.start('claw', config.socket_path)
        terminal.text('/extensions auth login ' + PROVIDER if cancel else '/login')
        terminal.keys('Enter')
        if not cancel:
            terminal.wait('Select a provider; no action yet')
            assert not calls
            terminal.keys('Down')
            terminal.wait('2/2')
            terminal.keys('Enter')
            terminal.wait('Choose an explicit authentication action')
            terminal.keys('Down')
            terminal.wait('2/4')
            terminal.keys('Enter')
        for index, phase in enumerate(('consent', 'on_auth', 'on_prompt')):
            terminal.wait('Provider authentication · ' + phase)
            terminal.resize(58 + index * 2, 18 + index)
            reviewed(terminal, phase)
            assert not calls
            terminal.keys('Enter')
        terminal.wait('sensitive input')
        terminal.text(CODE)
        terminal.wait('********')
        assert CODE not in terminal.visible() and CODE.encode() not in terminal.raw.read_bytes()
        terminal.keys('Escape' if cancel else 'Enter')
        terminal.wait_hidden('sensitive input')
        terminal.resize(80, 24)
        terminal.wait_cursor(True)
        terminal.text('UNSENT AFTER PROVIDER AUTH')
        terminal.wait_editor('UNSENT AFTER PROVIDER AUTH')
        terminal.finish()
        assert not calls and not owned['runtime']._active_session.agent.messages
        raw = terminal.raw.read_bytes()
        assert all(secret.encode() not in raw for secret in (ACCESS, REFRESH, CODE))
        for path in (tmp_path / 'data').rglob('*.json*'):
            assert all(secret not in path.read_text() for secret in (ACCESS, REFRESH, CODE))
    finally:
        terminal.call('kill-server')
        stop.set()
        worker.join(12)
        assert not worker.is_alive() and not errors
