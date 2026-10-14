"""Configured keymap host actions stay outside parent/child terminal hook data."""
from __future__ import annotations

import pytest
from xdog.coding.modes.rpc.terminal_data import editable_data
from xdog.tui.prompt_keymap import PromptKeymap


@pytest.mark.parametrize('action', ['send', 'external_editor', 'save_draft', 'ext-terminal-count'])
def test_remapped_edit_key_cannot_be_hook_input_or_output(action):
    overrides = {'left': [], action: 'left'}
    # Extension registration requires modified/function keys; use Ctrl+A for that case.
    if action.startswith('ext-'):
        overrides = {'start': [], action: 'ctrl+a'}
    keymap = PromptKeymap.from_overrides(overrides)
    value = '\x01' if action.startswith('ext-') else '\x1b[D'
    with pytest.raises(ValueError):
        editable_data(value, 'key', keymap=keymap)
    assert editable_data(value, 'paste', keymap=keymap) == value
    assert editable_data('ab', 'key', keymap=keymap) == 'ab'


def test_current_extension_shortcuts_use_native_conflict_and_disable_rules():
    from xdog.tui.extension_shortcuts import ExtensionShortcuts
    from xdog.tui.keys import KeyEvent

    rows = [{'extension': 'terminal', 'command': 'ext-terminal-count', 'key': 'ctrl+a', 'description': 'Count'}]
    unbound = PromptKeymap.from_overrides({'start': []})
    assert ExtensionShortcuts(rows, unbound).command(KeyEvent('a', ctrl=True)) == 'ext-terminal-count'
    with pytest.raises(ValueError):
        editable_data('\x01', 'key', keymap=unbound, shortcuts=rows)
    # Active native editor binding wins; the conflicting extension is disabled.
    assert ExtensionShortcuts(rows, PromptKeymap()).command(KeyEvent('a', ctrl=True)) is None
    assert editable_data('\x01', 'key', keymap=PromptKeymap(), shortcuts=rows) == '\x01'
    assert editable_data('text', 'key', keymap=unbound, shortcuts=rows) == 'text'
    assert editable_data('\x01', 'paste', keymap=unbound, shortcuts=rows) == '\x01'
