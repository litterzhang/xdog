"""Received RPC frames are actually painted/interacted with in an owned Linux terminal."""
from __future__ import annotations

import json
import shlex
import shutil
import sys
from pathlib import Path

import pytest

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Owned current Linux tmux terminal client')


@pytest.fixture
def rpc_terminal(tmp_path):
    owned = Terminal(tmp_path)
    try:
        yield owned
    finally:
        owned.call('kill-server')


@pytest.mark.parametrize('child', [False, True], ids=['parent', 'child'])
def test_actual_rpc_physical_frame_cursor_resize_nested_approval_and_restore(rpc_terminal, tmp_path, child):
    terminal = rpc_terminal
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/layout', root / 'layout')
    home = tmp_path / 'rpc-home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['layout'], 'extension_dir': str(root)}))
    client = Path(__file__).parent / 'fixtures/rpc_terminal_client.py'
    terminal.text(shlex.join([sys.executable, str(client), 'child' if child else 'parent']))
    terminal.keys('Enter')
    if child:
        terminal.wait('TOOL APPROVAL')
        assert 'STRUCTURED-LAYOUT' not in terminal.visible()
        terminal.text('not-approved')
        terminal.keys('Enter')
        terminal.wait('requires y')
        terminal.keys('C-u')
        terminal.text('y')
        terminal.keys('Enter')
    terminal.wait('STRUCTURED-LAYOUT')
    terminal.wait_cursor(True)
    terminal.text('\x1b[200~开发🙂\x1b[201~')
    terminal.wait('开发🙂')
    terminal.keys('F5')
    terminal.wait('LAYER-POPUP')
    terminal.text('\x1b[200~MUST-NOT-EDIT\x1b[201~')
    terminal.wait('0 pending')
    terminal.keys('F5')
    terminal.wait_hidden('LAYER-POPUP')
    terminal.resize(42, 18)
    terminal.wait('Draft')
    terminal.wait_cursor(True)
    terminal.resize(80, 24)
    terminal.wait('0 pending')
    terminal.wait_cursor(True)
    terminal.keys('F2')
    terminal.wait('Nested layout question')
    terminal.keys('C-u')
    terminal.text('PHYSICAL-NESTED')
    terminal.keys('Enter')
    terminal.wait('RPC nested returned')
    terminal.wait('STRUCTURED-LAYOUT')
    terminal.wait('0 pending')
    terminal.wait_cursor(True)
    terminal.keys('Enter')
    terminal.wait('RPC COMPLETE')
    terminal.text('parent restored after RPC')
    terminal.wait_editor('parent restored after RPC')
    terminal.finish()
    receipts = [json.loads(line) for line in (tmp_path / 'rpc-terminal-receipts.jsonl').read_text().splitlines()]
    inputs = [row for row in receipts if row['type'] in ('extension_custom_input', 'child_custom_input')]
    assert any(row['event']['kind'] == 'key' and row['event']['painted'] > 0 for row in inputs)
    assert any(row['event']['kind'] == 'resize' and row['event']['width'] == 42 for row in inputs)
    assert any(row['event']['kind'] == 'paste' and row['event']['text'] == '开发🙂' for row in inputs)
    assert all(row['event'].get('key') not in ('escape', 'c', 'd', 'l', 'z') for row in inputs)
    assert (tmp_path / 'rpc-terminal-result.json').exists()
    if not child:
        assert not (tmp_path / 'rpc-model').exists()
