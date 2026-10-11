"""Claw shipped TUI elicitation full review, private gateway/offline peer, no browser/model."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading

import pytest

from packages.claw.tests.test_mcp_elicitation_gateway import setup
from packages.coding.tests.test_mcp_elicitation_terminal import reviewed
from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(os.name != 'posix' or not shutil.which('tmux'), reason='Owned Linux tmux required')


@pytest.mark.parametrize('mode', ['form', 'url', 'recovery'])
def test_native_claw_elicitation_request_reply_resize_parent_and_termios(tmp_path, mode):
    ready, stop = threading.Event(), threading.Event()
    owned, errors = {}, []
    def serve():
        async def run():
            server = None
            try:
                server, config, runtime, session, adapter, log = await setup(
                    tmp_path, 'form' if mode == 'recovery' else mode)
                # Physical full-page reviews can legitimately outlast the small
                # unit-peer deadline, without changing production client bounds.
                from dataclasses import replace
                runtime.mcp.configs['owned'] = replace(runtime.mcp.configs['owned'], timeout=30)
                if mode == 'recovery':
                    peer = tmp_path / 'peer.py'
                    peer.write_text(peer.read_text().replace("'name':{'type':'string'}",
                                                            "'name':{'type':'string','minLength':5}"))
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
    if mode == 'url':
        helper = tmp_path / 'owned-url-helper.py'
        helper_log = tmp_path / 'owned-url-open.jsonl'
        helper.write_text('import sys,json\nfrom pathlib import Path\n'
                          + f"with Path({str(helper_log)!r}).open('a') as f:f.write(json.dumps(sys.argv[1])+'\\n')\n")
        terminal.child_env['XDOG_FIXTURE_URL_HELPER'] = str(helper)
    try:
        terminal.start('claw', owned['config'].socket_path)
        terminal.text('/mcp')
        terminal.keys('Enter')
        terminal.wait('owned · disconnected')
        terminal.text('f')
        terminal.wait('Elicitation armed')
        assert not owned['log'].exists() and not owned['adapter'].calls
        terminal.text('c')
        terminal.wait('Elicitation request')
        terminal.resize(60, 18)
        reviewed(terminal)
        if mode == 'url':
            assert not helper_log.exists()  # Painting is not opening consent.
            terminal.text('o')
            terminal.wait('Reviewed URL passed to desktop helper')
            terminal.text('o')
            terminal.call('display-message', '-p', '-t', 'test', '#{cursor_x}')
            assert [json.loads(row) for row in helper_log.read_text().splitlines()] == [
                'https://operator.example/flow']
            # Opening does not send a protocol accept or complete the interaction.
            assert not any(json.loads(row).get('id') == 'form' for row in owned['log'].read_text().splitlines())
        terminal.keys('Enter')
        if mode in {'form', 'recovery'}:
            terminal.wait('Elicitation form')
            if mode == 'recovery':
                terminal.keys('C-a', 'C-k')
                terminal.text('{"name":"a"}')
                terminal.keys('Enter')
                terminal.wait('Elicitation reply')
                reviewed(terminal)
                terminal.keys('Enter')
                terminal.wait('Reply did not satisfy requested schema')
                # No peer data; rejected original edits are retained for a new review.
                reviewed(terminal)
                terminal.keys('Enter')
                terminal.wait('Elicitation form')
                terminal.wait_editor('{"name":"a"}')
            terminal.keys('C-a', 'C-k')
            terminal.text('{"name":"USER REVIEWED CLAW"}')
            terminal.keys('Enter')
            terminal.wait('Elicitation reply')
            assert not any(json.loads(row).get('id') == 'form' for row in owned['log'].read_text().splitlines())
            reviewed(terminal)
            terminal.keys('Enter')
        terminal.wait('Elicitation response')
        assert not any(json.loads(row).get('id') == 'form' for row in owned['log'].read_text().splitlines())
        reviewed(terminal)
        terminal.keys('Enter')
        terminal.wait('Elicitation ended')
        if mode == 'url':
            terminal.wait('Peer reports external interaction complete')
        rows = [json.loads(row) for row in owned['log'].read_text().splitlines()]
        response = next(row for row in rows if row.get('id') == 'form')['result']
        assert response == ({'action': 'accept', 'content': {'name': 'USER REVIEWED CLAW'}}
                            if mode in {'form', 'recovery'}
                            else {'action': 'accept'})
        assert not owned['adapter'].calls and not owned['session'].agent.messages
        terminal.keys('Escape')
        terminal.wait_hidden('MCP servers')
        terminal.wait_cursor(True)
        terminal.text('PARENT AFTER CLAW ELICITATION')
        terminal.wait_editor('PARENT AFTER CLAW ELICITATION')
        terminal.finish()
    finally:
        terminal.call('kill-server')
        stop.set()
        thread.join(6)
        assert not thread.is_alive() and not errors
