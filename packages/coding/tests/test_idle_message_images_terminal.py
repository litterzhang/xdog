"""Physical Linux image-only idle message review, never composer capture."""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest
from xdog.ai.types import ImageContent
from xdog.coding.core.session_manager import SessionManager

from packages.agent.tests.test_idle_message_images import PNG
from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Owned current-Linux terminal required')


@pytest.mark.parametrize('approve', [False, True])
def test_coding_image_only_idle_request_full_descriptor_review_and_termios(tmp_path, approve):
    root, home = tmp_path / 'extensions', tmp_path / 'home'
    home.mkdir()
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/messagerequest',
                    root / 'messagerequest')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['messagerequest'], 'extension_dir': str(root)}))
    terminal = Terminal(tmp_path)
    terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    try:
        terminal.start('coding', '--model', 'terminal-fixture', '--permission-mode', 'deny')
        terminal.resize(100, 30)
        terminal.wait('ready |')
        terminal.text('/ext-messagerequest-request {"text":"","images":true}')
        terminal.keys('Enter')
        terminal.wait('Send this extension message')
        assert not (tmp_path / 'fixture-model-count').exists()
        if approve:
            terminal.keys('Right', 'Enter')
            terminal.wait('form review')
            terminal.resize(60, 18)
            seen = ''
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                visible = terminal.visible()
                seen += visible
                if 'Enter submits reviewed form' in visible:
                    break
                terminal.keys('NPage')
                time.sleep(.04)
            else:
                raise AssertionError(terminal.visible())
            assert 'image/png' in seen and 'sha256' in seen and PNG not in seen
            assert not (tmp_path / 'fixture-model-count').exists()
            terminal.keys('Enter')
            terminal.wait('IMAGE-RECEIVED-1')
            saved = SessionManager(home / 'sessions').get_most_recent()
            assert sum(isinstance(part, ImageContent) for part in saved.messages[0].content) == 1
            assert saved.messages[0].content[1].data == PNG
        else:
            terminal.keys('Escape')
            terminal.wait('MESSAGE-QUEUED')
            assert not (tmp_path / 'fixture-model-count').exists()
        terminal.wait_cursor(True)
        terminal.text('PARENT AFTER EXPLICIT IMAGE')
        terminal.wait_editor('PARENT AFTER EXPLICIT IMAGE')
        terminal.finish()
        assert PNG.encode() not in terminal.raw.read_bytes()
    finally:
        terminal.call('kill-server')
