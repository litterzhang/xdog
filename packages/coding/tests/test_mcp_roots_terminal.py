"""Owned physical Linux two-host roots review, native main-buffer/termios recovery."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import threading

import pytest

from packages.agent.tests.test_mcp_roots import ROOTS, write_peer
from packages.claw.tests.test_mcp_roots_gateway import setup
from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(os.name != 'posix' or not shutil.which('tmux'), reason='Owned Linux tmux required')


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_actual_root_withdrawal_full_review_resize_parent_and_termios(tmp_path, kind):
    terminal = Terminal(tmp_path)
    ready, stop = threading.Event(), threading.Event()
    owned, errors = {}, []
    thread = None
    if kind == 'coding':
        peer, log = tmp_path / 'peer.py', tmp_path / 'frames'
        write_peer(peer)
        home = tmp_path / 'coding-home'
        home.mkdir(mode=0o700)
        (home / 'settings.json').write_text(json.dumps({'mcp_servers': {'owned': {
            'command': sys.executable, 'args': [str(peer), str(log), 'legacy'], 'roots': ROOTS}}}))
        terminal.child_env['CODING_DIR'] = str(home)
    else:
        def serve():
            async def run():
                server = None
                try:
                    server, config, manager, calls = await setup(tmp_path)
                    owned.update(config=config, manager=manager, calls=calls)
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
    try:
        terminal.start(kind, *([owned['config'].socket_path] if kind == 'claw' else []))
        if kind == 'coding':
            terminal.text('/mcp connect owned')
            terminal.keys('Enter')
            terminal.wait('MCP owned: connect completed')
        terminal.text('/mcp')
        terminal.keys('Enter')
        terminal.wait('owned · connected')
        terminal.text('o')
        terminal.wait('JSON roots array')
        terminal.keys('Enter')
        terminal.wait('MCP root disclosure')
        terminal.resize(60, 18)
        # Resize invalidates paint evidence, not the original review or draft.
        for _ in range(20):
            if 'Enter confirms fully reviewed roots' in terminal.visible():
                break
            terminal.keys('NPage')
            __import__('time').sleep(.03)
        terminal.wait('Enter confirms fully reviewed roots')
        terminal.keys('Enter')
        terminal.wait('Root metadata updated')
        frames = [json.loads(line) for line in (tmp_path / 'frames').read_text().splitlines()]
        assert sum(row.get('method') == 'notifications/roots/list_changed' for row in frames) == 1
        assert not any(row.get('method') in {'tools/call', 'resources/read'} for row in frames)
        if kind == 'claw':
            assert owned['manager']._clients['owned'].root_capability.roots == ()
            assert not owned['calls']
        terminal.keys('Escape')
        terminal.wait_hidden('MCP servers')
        terminal.wait_cursor(True)
        terminal.text('PARENT AFTER ROOTS')
        terminal.wait_editor('PARENT AFTER ROOTS')
        terminal.finish()
    finally:
        terminal.call('kill-server')
        stop.set()
        if thread is not None:
            thread.join(6)
            assert not thread.is_alive() and not errors
