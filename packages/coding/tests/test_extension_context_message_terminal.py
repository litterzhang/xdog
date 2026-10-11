"""Physical current-Linux nextTurn review starts no model until the next user submits."""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest
from xdog.agent.core import CustomAgentMessage
from xdog.coding.core.session_manager import SessionManager

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='UI-049 current-Linux private terminal required')


@pytest.mark.parametrize('approve', [False, True])
def test_physical_coding_context_review_and_later_user_admission(tmp_path, approve):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/contextrequest',
                    root / 'contextrequest')
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['contextrequest'], 'extension_dir': str(root)}))
    terminal = Terminal(tmp_path)
    terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    try:
        terminal.start('coding', '--model', 'terminal-fixture', '--permission-mode', 'deny')
        terminal.resize(100, 30)
        terminal.wait('ready |')
        terminal.text('/ext-contextrequest-request')
        terminal.keys('Enter')
        terminal.wait('Retain this exact next-turn context')
        assert not (tmp_path / 'fixture-model-count').exists()
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
        else:
            terminal.keys('Escape')
        terminal.wait('CONTEXT-QUEUED')
        terminal.wait('ready |')
        assert not (tmp_path / 'fixture-model-count').exists()
        terminal.text('Next explicit user prompt')
        terminal.keys('Enter')
        terminal.wait('FIXTURE-ANSWER')
        terminal.wait('ready |')
        saved = SessionManager(home / 'sessions').get_most_recent()
        custom = [message for message in saved.messages if isinstance(message, CustomAgentMessage)]
        assert bool(custom) is approve
        if approve:
            assert len(custom) == 1 and custom[0].data['custom_type'] == 'note'
            assert custom[0].data['details'] == {'opaque': 'PRIVATE-NOT-MODEL'}
        assert (tmp_path / 'fixture-model-count').read_text() == '1'
        terminal.wait_cursor(True)
        terminal.text('UNSENT AFTER CUSTOM CONTEXT')
        terminal.wait_editor('UNSENT AFTER CUSTOM CONTEXT')
        terminal.finish()
    finally:
        terminal.call('kill-server')
