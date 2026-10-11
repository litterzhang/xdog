"""Current-Linux physical queue review uses actual provider+tool worker and parent ownership."""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest
from xdog.ai.types import ImageContent
from xdog.coding.core.session_manager import SessionManager

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='UI-049 owned current-Linux terminal required')


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('approve', [False, True])
def test_physical_coding_queue_review_preserves_original_owner_and_typed_image(tmp_path, delivery, approve):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/queuedhost', root / 'queuedhost')
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['queuedhost'], 'extension_dir': str(root)}))
    terminal = Terminal(tmp_path)
    terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    try:
        terminal.start('coding', '--model', 'ext-queuedhost-offline/queue', '--permission-mode', 'allow-all',
                       expected='coding | ext-queuedhost')
        terminal.resize(100, 30)
        terminal.text('QUEUE-START IMAGE ' + ('STEER' if delivery == 'steer' else 'FOLLOW'))
        terminal.keys('Enter')
        terminal.wait('Queue this exact extension input')
        assert not (tmp_path / 'fixture-model-count').exists()  # No native fallback.
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
        terminal.wait('QUEUE-PHYSICAL-DONE')
        terminal.wait('ready |')
        store = SessionManager(home / 'sessions')
        saved = store.get_most_recent()
        queued = [row for row in saved.messages if row.role == 'user' and 'REVIEWED-QUEUE' in str(row.content)]
        assert bool(queued) is approve
        if approve:
            assert len(queued) == 1 and queued[0].content[0].text.startswith(
                '[User-approved message requested by extension queuedhost]')
            assert sum(isinstance(part, ImageContent) for part in queued[0].content) == 1
        assert not (tmp_path / 'fixture-model-count').exists()
        terminal.wait_cursor(True)
        terminal.text('UNSENT AFTER QUEUE REVIEW')
        terminal.wait_editor('UNSENT AFTER QUEUE REVIEW')
        terminal.finish()
    finally:
        terminal.call('kill-server')
