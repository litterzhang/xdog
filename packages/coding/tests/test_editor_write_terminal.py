"""Private current-Linux physical proposal review and native terminal restoration."""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Owned current-Linux terminal required')


@pytest.mark.parametrize('approve', [False, True])
def test_physical_coding_editor_write_full_review_resize_unicode_and_no_model(tmp_path, approve):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/editorwrite', root / 'editorwrite')
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['editorwrite'], 'extension_dir': str(root)}))
    terminal = Terminal(tmp_path)
    terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    try:
        terminal.start('coding', '--model', 'terminal-fixture', '--permission-mode', 'deny')
        terminal.wait('ready |')
        terminal.text('/ext-editorwrite-write')
        terminal.keys('Enter')
        terminal.wait('Replace this owner')
        assert not (tmp_path / 'fixture-model-count').exists()
        if approve:
            terminal.keys('Right', 'Enter')
            terminal.wait('form review')
            terminal.resize(45, 18)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if 'Enter submits reviewed form' in terminal.visible():
                    break
                terminal.keys('NPage')
                time.sleep(.04)
            else:
                raise AssertionError(terminal.visible())
            terminal.keys('Enter')
            terminal.wait('Reviewed extension draft applied')
            terminal.wait_editor('unsent')
            assert 'PROPOSED 界' in terminal.visible()
        else:
            terminal.keys('Escape')
            terminal.wait('EDITOR-WRITE-False')
            terminal.text('OWNED DRAFT AFTER DENIAL')
            terminal.wait_editor('OWNED DRAFT AFTER DENIAL')
        assert not (tmp_path / 'fixture-model-count').exists()
        terminal.finish()
    finally:
        terminal.call('kill-server')
