"""Both shipped native clients require physical context approval before model execution."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading

import pytest

from packages.agent.tests.test_mcp_sampling_registration import config as peer_config
from packages.claw.tests.test_mcp_sampling_gateway import setup
from packages.coding.tests.test_mcp_sampling_terminal import full_review
from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(os.name != 'posix' or not shutil.which('tmux'), reason='Owned Linux tmux required')


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_native_extra_context_three_painted_phases_and_recovery(tmp_path, kind):
    ready, stop = threading.Event(), threading.Event()
    owned, errors = {}, []
    thread = None
    terminal = Terminal(tmp_path)
    if kind == 'coding':
        configs, log = peer_config(tmp_path)
        configs['owned']['timeout'] = 30
        home = tmp_path / 'home'
        home.mkdir(mode=0o700)
        (home / 'settings.json').write_text(json.dumps({'mcp_servers': configs}))
        terminal.child_env['CODING_DIR'] = str(home)
        terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    else:
        def serve():
            async def run():
                server = None
                try:
                    server, config, runtime, session, adapter, log = await setup(tmp_path)
                    owned.update(config=config, runtime=runtime, session=session, adapter=adapter, log=log)
                    ready.set()
                    while not stop.is_set():
                        await asyncio.sleep(.01)
                except BaseException as exc:
                    errors.append(exc)
                    ready.set()
                finally:
                    if server is not None:
                        await server.stop()
            asyncio.run(run())
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        assert ready.wait(6) and not errors
        log = owned['log']
    peer = tmp_path / 'peer.py'
    peer.write_text(peer.read_text().replace("'maxTokens':20", "'maxTokens':20,'includeContext':'thisServer'"))
    def no_model():
        if kind == 'coding':
            assert not (tmp_path / 'fixture-model-count').exists()
        else:
            assert not owned['adapter'].calls
    try:
        terminal.start(kind, *([owned['config'].socket_path] if kind == 'claw' else []))
        terminal.text('/mcp')
        terminal.keys('Enter')
        terminal.wait('owned · disconnected')
        terminal.text('b')
        terminal.wait('Sampling armed')
        terminal.text('c')
        terminal.wait('Sampling input review')
        terminal.keys('C-a', 'C-k')
        terminal.text(json.dumps({'model': 'terminal-fixture' if kind == 'coding' else 'explicit-model',
                                 'max_tokens': 10, 'context': 'thisServer', 'tools': []}))
        terminal.keys('Enter')
        full_review(terminal, 'input')
        no_model()
        terminal.keys('Enter')
        terminal.wait('Sampling context review')
        no_model()
        terminal.resize(60, 18)
        full_review(terminal, 'context')
        no_model()  # Even a fully painted disclosure has not executed yet.
        terminal.keys('Enter')
        terminal.wait('Sampling output review')
        if kind == 'coding':
            assert (tmp_path / 'fixture-model-count').read_text() == '1'
        else:
            assert len(owned['adapter'].calls) == 1
        frames = [json.loads(row) for row in log.read_text().splitlines()]
        assert not any(row.get('id') == 'sample' and 'result' in row for row in frames)
        full_review(terminal, 'output')
        terminal.keys('Enter')
        terminal.wait('Sampling request ended')
        terminal.keys('Escape')
        terminal.wait_hidden('MCP servers')
        terminal.wait_cursor(True)
        terminal.text('PARENT AFTER CONTEXT REVIEW')
        terminal.wait_editor('PARENT AFTER CONTEXT REVIEW')
        terminal.finish()
    finally:
        terminal.call('kill-server')
        stop.set()
        if thread is not None:
            thread.join(6)
            assert not thread.is_alive() and not errors
