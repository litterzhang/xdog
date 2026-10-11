"""Physical Linux extension message review retains literal text and terminal ownership."""
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
def test_physical_coding_message_review_starts_exact_labeled_turn_only_with_consent(tmp_path, approve):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/messagerequest',
                    root / 'messagerequest')
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['messagerequest'], 'extension_dir': str(root)}))
    terminal = Terminal(tmp_path)
    terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    try:
        terminal.start('coding', '--model', 'terminal-fixture', '--permission-mode', 'deny')
        terminal.resize(100, 30)
        terminal.wait('ready |')
        terminal.text('/ext-messagerequest-request {"text":"/quit"}')
        terminal.keys('Enter')
        terminal.wait('Send this extension message')
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
            assert not (tmp_path / 'fixture-model-count').exists()
            terminal.keys('Enter')
            terminal.wait('FIXTURE-ANSWER')
            assert (tmp_path / 'fixture-model-count').read_text() == '1'
            store = SessionManager(home / 'sessions')
            saved = next(store.load_session(row.session_id) for row in store.list_sessions()
                         if store.load_session(row.session_id).messages)
            assert saved.messages[0].content[0].text == (
                '[User-approved message requested by extension messagerequest]\n\n/quit')
        else:
            terminal.keys('Escape')
            terminal.wait('MESSAGE-QUEUED')
            assert not (tmp_path / 'fixture-model-count').exists()
        terminal.wait_cursor(True)
        terminal.text('UNSENT AFTER MESSAGE REVIEW')
        terminal.wait_editor('UNSENT AFTER MESSAGE REVIEW')
        terminal.finish()
    finally:
        terminal.call('kill-server')
