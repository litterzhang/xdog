"""Physical Coding RPC receiver full-review and incomplete/canceled recovery."""
from __future__ import annotations

import json
import shlex
import shutil
import sys
import time
from pathlib import Path

import pytest

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Current Linux private terminal required')


@pytest.mark.parametrize('end', ['recover', 'cancel', 'incomplete'])
@pytest.mark.parametrize('images', [False, True])
def test_rpc_parent_actual_terminal_review_resize_error_recovery_and_termios(tmp_path, end, images):
    terminal = Terminal(tmp_path)
    try:
        fixture = Path(__file__).parent / 'fixtures/rpc_parent_draft_client.py'
        terminal.text(shlex.join([sys.executable, str(fixture), end, 'image' if images else 'text']))
        terminal.keys('Enter')
        terminal.wait('RPC PARENT READY')
        terminal.keys('F8')
        if end == 'incomplete':
            terminal.wait('RPC parent incomplete receipt refused')
        else:
            terminal.wait('RPC parent full receipt review')
            terminal.resize(60, 18)
            if end == 'cancel':
                terminal.keys('Escape')
                terminal.wait('RPC parent recovery canceled')
            else:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if 'Enter approves exact output' in terminal.visible():
                        break
                    terminal.keys('NPage')
                    time.sleep(.04)
                else:
                    raise AssertionError(terminal.visible())
                assert not (tmp_path / 'rpc-model').exists()
                terminal.keys('Enter')
                terminal.wait_editor('RPC-PHYSICAL-RECOVERED')
                terminal.wait('1 images' if images else '0 images')
                terminal.wait('NOT SENT')
        if end != 'recover':
            terminal.text('SAFE AFTER RPC RECOVERY REFUSAL')
            terminal.wait_editor('SAFE AFTER RPC RECOVERY REFUSAL')
        terminal.finish()
        assert not (tmp_path / 'rpc-model').exists()
        wire = [json.loads(line) for line in (tmp_path / 'rpc-parent-wire.jsonl').read_text().splitlines()]
        assert not any(row.get('type') == 'prompt' for row in wire)
        assert sum(row.get('action') == 'read' for row in wire) == 1
    finally:
        terminal.call('kill-server')
