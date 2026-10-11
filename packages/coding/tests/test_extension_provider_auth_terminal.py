"""Offline auth binding through both shipped UIs on a private Linux terminal."""
from __future__ import annotations

import asyncio
import json
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

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Owned current-Linux terminal required')


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('failure', [False, True])
def test_physical_auth_failure_or_success_keeps_secrets_private_and_parent_editable(
        tmp_path, monkeypatch, kind, failure):
    secret = 'offline-terminal-ui044-private-token'
    # Inherit the sentinel through the private tmux server environment, not a
    # literal shell command that the test harness itself would print to the pane.
    monkeypatch.setenv('XDOG_UI044_TEST_KEY', secret)
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/providerauth', root / 'providerauth')
    terminal = Terminal(tmp_path)
    errors, calls = [], []
    ready, stop = threading.Event(), threading.Event()
    worker = None
    alias = 'ext-providerauth-local/echo'
    settings = {'mode': 'fail' if failure else ''}
    if kind == 'coding':
        home = tmp_path / 'home'
        home.mkdir()
        (home / 'settings.json').write_text(json.dumps({'extensions': ['providerauth'],
            'extension_dir': str(root), 'extension_settings': {'providerauth': settings}}))
        terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    else:
        config = ClawConfig(model=alias, data_dir=str(tmp_path / 'data'), socket_path=str(tmp_path / 'socket'),
            pid_file=str(tmp_path / 'pid'), groups=(GroupDef(id='main', is_main=True),),
            extensions=('providerauth',), extension_dir=str(root), extension_settings={'providerauth': settings})

        def base(*args):
            calls.append(args)
            return EventStream.empty(AssistantMessage())

        def serve():
            async def run():
                server = GatewayServer(config)
                try:
                    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(alias, base)):
                        await server.start()
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
        assert ready.wait(8) and not errors
    try:
        terminal.start(kind, *(['--model', alias] if kind == 'coding' else [config.socket_path]),
                       expected='coding | ext-providerauth' if kind == 'coding' else 'connected')
        terminal.text('/ext-providerauth-count')
        terminal.keys('Enter')
        terminal.wait('CALLS=0;RESOLUTIONS=0')
        terminal.text('explicit authenticated offline turn')
        terminal.keys('Enter')
        terminal.wait('Extension provider credentials unavailable' if failure else 'AUTHENTICATED-OFFLINE-ANSWER')
        terminal.wait('ready' if kind == 'coding' else 'idle')
        terminal.keys('C-c')  # Claw may retain a failed draft; do not replay it.
        terminal.text('/ext-providerauth-count')
        terminal.keys('Enter')
        terminal.wait(f'CALLS={0 if failure else 1};RESOLUTIONS=1')
        terminal.resize(46, 12)
        terminal.resize(80, 24)
        terminal.text('parent after auth 确认')
        terminal.wait_editor('parent after auth 确认')
        assert secret not in terminal.pane() and secret.encode() not in terminal.raw.read_bytes()
        assert not calls and not (tmp_path / 'fixture-model-count').exists()
        terminal.finish()
    finally:
        terminal.call('kill-server')
        if worker is not None:
            stop.set()
            worker.join(6)
            assert not worker.is_alive() and not errors
