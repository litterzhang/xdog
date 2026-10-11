"""Physical Coding sampling dual consent, offline adapter/owned peer/main-buffer recovery."""
from __future__ import annotations

import json
import os
import shutil
import time

import pytest

from packages.agent.tests.test_mcp_sampling_registration import config
from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(os.name != 'posix' or not shutil.which('tmux'), reason='Owned Linux tmux required')


def full_review(terminal, phase):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if 'Enter approves exact ' + phase in terminal.visible():
            return
        terminal.keys('NPage')
        time.sleep(.05)
    raise AssertionError('Sampling full review did not paint:\n' + terminal.visible())


@pytest.mark.parametrize('release', [False, True])
def test_actual_coding_sampling_input_output_review_resize_cancel_and_termios(tmp_path, release):
    configs, log = config(tmp_path)
    configs['owned']['timeout'] = 30
    home = tmp_path / 'coding-home'
    home.mkdir(mode=0o700)
    (home / 'settings.json').write_text(json.dumps({'mcp_servers': configs}))
    terminal = Terminal(tmp_path)
    terminal.child_env['CODING_DIR'] = str(home)
    terminal.child_env['XDOG_FIXTURE_COUNT_MODEL'] = '1'
    try:
        terminal.start('coding')
        terminal.text('/mcp')
        terminal.keys('Enter')
        terminal.wait('owned · disconnected')
        terminal.text('b')
        terminal.wait('Sampling armed for this view only')
        assert not log.exists() and not (tmp_path / 'fixture-model-count').exists()
        terminal.text('c')
        terminal.wait('Sampling input review')
        assert not (tmp_path / 'fixture-model-count').exists()
        terminal.keys('C-a', 'C-k')
        terminal.text(json.dumps({'model': 'terminal-fixture', 'max_tokens': 20, 'context': 'none', 'tools': []}))
        terminal.keys('Enter')
        terminal.wait('Approve exact request')
        terminal.resize(60, 18)
        full_review(terminal, 'input')
        assert not (tmp_path / 'fixture-model-count').exists()
        terminal.keys('Enter')
        terminal.wait('Sampling output review')
        assert (tmp_path / 'fixture-model-count').read_text() == '1'
        frames = [json.loads(row) for row in log.read_text().splitlines()]
        assert not any(row.get('id') == 'sample' for row in frames)
        if release:
            full_review(terminal, 'output')
            terminal.keys('Enter')
            terminal.wait('Sampling request ended')
            frames = [json.loads(row) for row in log.read_text().splitlines()]
            replies = [row for row in frames if row.get('id') == 'sample']
            assert len(replies) == 1 and replies[0]['result']['content']['text'] == 'FIXTURE-ANSWER'
        else:
            terminal.keys('Escape')
            terminal.wait('Sampling revoked')
            frames = [json.loads(row) for row in log.read_text().splitlines()]
            assert not any('result' in row and row.get('id') == 'sample' for row in frames)
        terminal.keys('Escape')
        terminal.wait_hidden('MCP servers')
        terminal.wait_cursor(True)
        terminal.text('PARENT AFTER SAMPLING')
        terminal.wait_editor('PARENT AFTER SAMPLING')
        terminal.finish()
        assert (tmp_path / 'fixture-model-count').read_text() == '1'
    finally:
        terminal.call('kill-server')
