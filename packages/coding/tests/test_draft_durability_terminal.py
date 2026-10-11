"""Physical Linux native composer durability labels, private tmux and offline clients."""
from __future__ import annotations

import os
import shutil

import pytest

from packages.coding.tests.test_terminal_acceptance import Terminal

pytestmark = pytest.mark.skipif(os.name != 'posix' or not shutil.which('tmux'), reason='Owned Linux tmux evidence')


@pytest.mark.parametrize('kind', ['coding', 'claw'])
def test_native_parent_autosave_durability_resize_cursor_and_termios(tmp_path, kind):
    terminal = Terminal(tmp_path)
    try:
        if kind == 'coding':
            terminal.start(kind)
        else:
            terminal.start(kind, str(tmp_path / 'missing.sock'), expected='reconnecting')
        terminal.text('/draft autosave on')
        terminal.keys('Enter')
        terminal.wait('PRIVATE PLAINTEXT')
        terminal.text('UNSENT DURABILITY TEXT')
        terminal.wait('parent draft:committed')
        terminal.wait_editor('UNSENT DURABILITY TEXT')
        terminal.resize(60, 18)
        terminal.wait('parent draft:committed')
        terminal.wait_editor('UNSENT DURABILITY TEXT')
        terminal.text(' EDIT')
        terminal.wait('parent draft:pending')
        terminal.wait('parent draft:committed')
        terminal.wait_editor('UNSENT DURABILITY TEXT EDIT')
        assert 'FIXTURE-ANSWER' not in terminal.pane()  # No submission/model replay.
        terminal.finish()
    finally:
        terminal.call('kill-server')
