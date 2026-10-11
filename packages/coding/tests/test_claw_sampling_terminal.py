"""Claw shipped TUI physical dual sampling review, private gateway/offline adapter."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading

import pytest

from packages.claw.tests.test_mcp_sampling_gateway import setup
from packages.coding.tests.test_mcp_sampling_terminal import full_review
from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(os.name != 'posix' or not shutil.which('tmux'), reason='Owned Linux tmux required')


@pytest.mark.parametrize('release', [False, True])
def test_claw_sampling_full_input_output_review_resize_and_terminal_recovery(tmp_path, release):
    ready, stop = threading.Event(), threading.Event()
    owned, errors = {}, []
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
    terminal = Terminal(tmp_path)
    try:
        terminal.start('claw', owned['config'].socket_path)
        terminal.text('/mcp')
        terminal.keys('Enter')
        terminal.wait('owned · disconnected')
        terminal.text('b')
        terminal.wait('Sampling armed')
        assert not owned['adapter'].calls and not owned['log'].exists()
        terminal.text('c')
        terminal.wait('Sampling input review')
        terminal.keys('C-a', 'C-k')
        terminal.text(json.dumps({'model': 'explicit-model', 'max_tokens': 10, 'context': 'none', 'tools': []}))
        terminal.keys('Enter')
        terminal.wait('Approve exact request')
        terminal.resize(60, 18)
        full_review(terminal, 'input')
        assert not owned['adapter'].calls
        terminal.keys('Enter')
        terminal.wait('Sampling output review')
        assert len(owned['adapter'].calls) == 1 and not owned['session'].agent.messages
        frames = [json.loads(row) for row in owned['log'].read_text().splitlines()]
        assert not any(row.get('id') == 'sample' for row in frames)
        if release:
            full_review(terminal, 'output')
            terminal.keys('Enter')
            terminal.wait('Sampling request ended')
            frames = [json.loads(row) for row in owned['log'].read_text().splitlines()]
            returned = next(row for row in frames if row.get('id') == 'sample')
            assert returned['result']['content']['text'] == 'MODEL OUTPUT'
        else:
            terminal.keys('Escape')
            terminal.wait('Sampling control settled')
            frames = [json.loads(row) for row in owned['log'].read_text().splitlines()]
            assert not any(row.get('id') == 'sample' and 'result' in row for row in frames)
        terminal.keys('Escape')
        terminal.wait_hidden('MCP servers')
        terminal.wait_cursor(True)
        terminal.text('PARENT AFTER CLAW SAMPLING')
        terminal.wait_editor('PARENT AFTER CLAW SAMPLING')
        terminal.finish()
        assert len(owned['adapter'].calls) == 1 and not owned['session'].agent.messages
    finally:
        terminal.call('kill-server')
        stop.set()
        thread.join(6)
        assert not thread.is_alive() and not errors
