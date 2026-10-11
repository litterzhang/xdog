"""Owned Linux native OAuth phases: private code never appears in terminal bytes."""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest

from packages.agent.tests.test_extension_provider_oauth import ACCESS, CODE, PROVIDER, REFRESH
from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(not sys.platform.startswith('linux') or not shutil.which('tmux'),
                               reason='Owned current-Linux terminal required')


def reviewed(terminal, phase):
    terminal.wait('Provider authentication · ' + phase)
    deadline = time.monotonic() + 10
    marker = 'o opens once' if phase == 'on_auth' else 'Enter approves exact output'
    while time.monotonic() < deadline:
        if marker in terminal.visible():
            return
        terminal.keys('NPage')
        time.sleep(.04)
    raise AssertionError('Provider full review not painted:\n' + terminal.visible())


@pytest.mark.parametrize('cancel', [False, True])
def test_physical_coding_provider_oauth_review_masking_resize_and_parent_termios(tmp_path, cancel):
    root, home = tmp_path / 'extensions', tmp_path / 'home'
    home.mkdir()
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/provideroauth', root / 'provideroauth')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['provideroauth'], 'extension_dir': str(root)}))
    terminal = Terminal(tmp_path)
    terminal.child_env.update(CODING_DIR=str(home), XDOG_FIXTURE_COUNT_MODEL='1')
    try:
        terminal.start('coding')
        terminal.text('/extensions auth login ' + PROVIDER if cancel else '/login')
        terminal.keys('Enter')
        if not cancel:
            terminal.wait('Select a provider; no action yet')
            assert not (tmp_path / 'fixture-model-count').exists()
            terminal.keys('Down')
            terminal.wait('2/2')
            terminal.keys('Enter')
            terminal.wait('Choose an explicit authentication action')
            terminal.keys('Down')
            terminal.wait('2/4')
            terminal.keys('Enter')
        for index, phase in enumerate(('consent', 'on_auth', 'on_prompt')):
            terminal.wait('Provider authentication · ' + phase)
            terminal.resize(58 + index * 2, 18 + index)
            reviewed(terminal, phase)
            assert not (tmp_path / 'fixture-model-count').exists()
            terminal.keys('Enter')
        terminal.wait('sensitive input')
        terminal.text(CODE)
        terminal.wait('********')
        assert CODE not in terminal.visible() and CODE.encode() not in terminal.raw.read_bytes()
        terminal.keys('Escape' if cancel else 'Enter')
        terminal.wait('ready')
        terminal.wait_hidden('sensitive input')
        terminal.resize(80, 24)
        terminal.wait_cursor(True)
        terminal.text('parent after provider auth')
        terminal.wait_editor('parent after provider auth')
        assert not (tmp_path / 'fixture-model-count').exists()
        terminal.finish()
        raw = terminal.raw.read_bytes()
        assert all(secret.encode() not in raw for secret in (ACCESS, REFRESH, CODE))
        for path in home.rglob('*.json'):
            assert all(secret not in path.read_text() for secret in (ACCESS, REFRESH, CODE))
    finally:
        terminal.call('kill-server')
