"""Physical original-parent after-run selection uses existing idle command and keeps focus."""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest
from xdog.coding.core.session_manager import SessionManager

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='UI-049 owned current-Linux terminal required')


@pytest.mark.parametrize('approve', [False, True])
def test_physical_coding_tool_selection_after_original_run(tmp_path, approve):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/aftertools', root / 'aftertools')
    with (root / 'aftertools/main.py').open('a') as stream:
        stream.write('''
    async def provider(model, context, options):
        last = context['messages'][-1]
        if last['role'] == 'user' and 'TOOLS-START' in str(last['content']):
            tool = next(tool for tool in context['tools'] if tool['name'].startswith('ext_aftertools_select'))
            yield {'type': 'tool_call', 'id': 'select-physical', 'name': tool['name'], 'arguments': {'names': []}}
        else:
            yield {'type': 'text', 'text': 'TOOLS-PARENT-FINISHED'}
        yield {'type': 'done'}
    api.register_provider('offline', [{'id': 'tools', 'context_window': 32000, 'max_tokens': 2048}], provider)
''')
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['aftertools'], 'extension_dir': str(root)}))
    terminal = Terminal(tmp_path)
    terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    try:
        terminal.start('coding', '--model', 'ext-aftertools-offline/tools', '--permission-mode', 'allow-all',
                       expected='coding | ext-aftertools')
        terminal.resize(100, 30)
        terminal.text('TOOLS-START')
        terminal.keys('Enter')
        terminal.wait('Queue this active-tool selection')
        store = SessionManager(home / 'sessions')
        saved = store.get_most_recent()
        assert saved is None or saved.active_tools is None
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
            terminal.keys('Enter')
            terminal.wait('Active tool selection applied')
        else:
            terminal.keys('Escape')
            terminal.wait('TOOLS-PARENT-FINISHED')
        terminal.wait('ready |')
        assert store.get_most_recent().active_tools == (() if approve else None)
        assert not (tmp_path / 'fixture-model-count').exists()
        terminal.wait_cursor(True)
        terminal.text('UNSENT AFTER TOOL SELECTION')
        terminal.wait_editor('UNSENT AFTER TOOL SELECTION')
        terminal.finish()
    finally:
        terminal.call('kill-server')
