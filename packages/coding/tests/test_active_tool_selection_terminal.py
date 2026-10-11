"""Physical Coding active tool review, no permission/model execution implied."""
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
def test_physical_coding_active_tool_selection_requires_complete_review(tmp_path, approve):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/toolselector', root / 'toolselector')
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['toolselector'], 'extension_dir': str(root)}))
    terminal = Terminal(tmp_path)
    terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    try:
        terminal.start('coding')
        terminal.text('/ext-toolselector-select {"names":[]}')
        terminal.keys('Enter')
        terminal.wait('Queue this active-tool selection')
        if approve:
            terminal.keys('Right', 'Enter')
            terminal.wait('form review')
            terminal.resize(60, 18)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if 'Enter submits reviewed form' in terminal.visible():
                    break
                terminal.keys('NPage')
                time.sleep(.04)
            else:
                raise AssertionError(terminal.visible())
            assert not (tmp_path / 'fixture-model-count').exists()
            terminal.keys('Enter')
            terminal.wait('Active tool selection applied')
            terminal.text('/ext-toolselector-inspect')
            terminal.keys('Enter')
            terminal.wait('ACTIVE-TOOL-COUNT-0')
            terminal.resize(100, 30)
            terminal.text('/tools')
            terminal.keys('Enter')
            terminal.wait('Selection: explicit')
            terminal.wait('selected 0 /')
            terminal.text('/')
            terminal.text('filesystem')
            terminal.keys('Enter')
            terminal.wait('Selection: excluded')
            terminal.keys('Escape')
            terminal.wait_hidden('Tools (inline)')

        else:
            terminal.keys('Escape')
            terminal.wait_hidden('Queue this active-tool selection')
            terminal.wait('TOOLS-QUEUED')
            assert 'Active tool selection applied' not in terminal.pane()
        terminal.wait_cursor(True)
        terminal.text('PARENT AFTER TOOL REVIEW')
        terminal.wait_editor('PARENT AFTER TOOL REVIEW')
        terminal.finish()
        assert not (tmp_path / 'fixture-model-count').exists()
    finally:
        terminal.call('kill-server')
