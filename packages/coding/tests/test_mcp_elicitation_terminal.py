"""Physical Coding elicitation reviews, owned peer and no model/browser admission."""
from __future__ import annotations

import json
import os
import shutil
import time

import pytest

from packages.agent.tests.test_mcp_elicitation_registration import peer_config
from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(os.name != 'posix' or not shutil.which('tmux'), reason='Owned Linux tmux required')


def reviewed(terminal):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if ('Enter continues reviewed data' in terminal.visible()
                or 'o opens reviewed URL once' in terminal.visible()):
            return
        terminal.keys('NPage')
        time.sleep(.04)
    raise AssertionError('Elicitation full review not painted:\n' + terminal.visible())


@pytest.mark.parametrize('mode', ['form', 'url', 'recovery'])
def test_actual_coding_elicitation_reviews_resize_parent_and_termios(tmp_path, mode):
    configs, log = peer_config(tmp_path)
    configs['owned']['timeout'] = 30
    if mode == 'url':
        peer = tmp_path / 'peer.py'
        peer.write_text(peer.read_text().replace("'message':'Review name','requestedSchema':{'type':'object',\n"
            "                'properties':{'name':{'type':'string'}},'required':['name']}",
            "'mode':'url','message':'Review external interaction','elicitationId':'external',"
            "'url':'https://operator.example/flow'"))
    if mode == 'url':
        peer = tmp_path / 'peer.py'
        peer.write_text(peer.read_text().replace("    elif method=='ping':",
            '''    elif row.get('id')=='form' and 'result' in row:
        for key in ['foreign', 'external', 'external']:
            send({'jsonrpc':'2.0','method':'notifications/elicitation/complete','params':{'elicitationId':key}})
    elif method=='ping':'''))
    if mode == 'recovery':
        peer = tmp_path / 'peer.py'
        peer.write_text(peer.read_text().replace("'name':{'type':'string'}",
                                                "'name':{'type':'string','minLength':5}"))
    home = tmp_path / 'coding-home'
    home.mkdir(mode=0o700)
    (home / 'settings.json').write_text(json.dumps({'mcp_servers': configs}))
    terminal = Terminal(tmp_path)
    if mode == 'url':
        helper = tmp_path / 'owned-url-helper.py'
        helper_log = tmp_path / 'owned-url-open.jsonl'
        helper.write_text('import sys,json\nfrom pathlib import Path\n'
                          + f"with Path({str(helper_log)!r}).open('a') as f:f.write(json.dumps(sys.argv[1])+'\\n')\n")
        terminal.child_env['XDOG_FIXTURE_URL_HELPER'] = str(helper)
    terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    try:
        terminal.start('coding')
        terminal.text('/mcp')
        terminal.keys('Enter')
        terminal.wait('owned · disconnected')
        terminal.text('f')
        terminal.wait('Elicitation armed for this view only')
        assert not log.exists()
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
            assert not any(json.loads(row).get('id') == 'form' for row in log.read_text().splitlines())
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
            terminal.text('{"name":"USER REVIEWED FORM"}')
            terminal.keys('Enter')
            terminal.wait('Elicitation reply')
            assert not any(json.loads(row).get('id') == 'form' for row in log.read_text().splitlines())
            reviewed(terminal)
            terminal.keys('Enter')
        terminal.wait('Elicitation response')
        assert not any(json.loads(row).get('id') == 'form' for row in log.read_text().splitlines())
        reviewed(terminal)
        terminal.keys('Enter')
        terminal.wait('Elicitation ended')
        if mode == 'url':
            terminal.wait('Peer reports external interaction complete')
        rows = [json.loads(row) for row in log.read_text().splitlines()]
        reply = next(row for row in rows if row.get('id') == 'form')['result']
        assert reply == ({'action': 'accept', 'content': {'name': 'USER REVIEWED FORM'}} if mode in {'form', 'recovery'}
                         else {'action': 'accept'})
        assert not (tmp_path / 'fixture-model-count').exists()
        terminal.keys('Escape')
        terminal.wait_hidden('MCP servers')
        terminal.wait_cursor(True)
        terminal.text('PARENT AFTER ELICITATION')
        terminal.wait_editor('PARENT AFTER ELICITATION')
        terminal.finish()
        assert not (tmp_path / 'fixture-model-count').exists()
    finally:
        terminal.call('kill-server')
