from __future__ import annotations

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.commands import declaration


def test_owned_command_shortcut_disposers_replacement_identity_and_case_capacity_validation():
    api = ExtensionAPI({})
    def callback(*args):
        return 'original'
    old = api.command('target', 'original', callback)
    new = api.command('target', 'new', lambda *args: 'new', replace=True)
    old()
    assert api.commands['target'][0] == 'new'
    new()
    new()
    assert 'target' not in api.commands
    binding = api.shortcut('target', 'f6', 'keyed', callback)
    newer = api.command('target', 'replacement', callback, replace=True)
    binding()
    assert not api.shortcuts and api.commands['target'][0] == 'replacement'
    newer()
    for index in range(32):
        api.command('cmd' + str(index), 'description', callback)
    with pytest.raises(ValueError):
        api.command('extra', 'description', callback)
    with pytest.raises(ValueError):
        api.command('CMD0', 'different spelling', callback)
    with pytest.raises(ValueError):
        api.command('cmd0', 'bad', callback, replace='yes')


def test_bad_live_command_declarations_cannot_add_callbacks_dangling_shortcuts_or_duplicate_aliases() -> None:

    def _check_case(changes):
        with pytest.raises(ValueError):
            declaration({**ExtensionAPI({}).command_snapshot(), **changes})

    for changes in [
        {"revision": True},
        {"commands": [{"name": "x", "description": 3}]},
        {"shortcuts": [{"name": "missing", "key": "f6", "description": "missing"}]},
        {"commands": [{"name": "x", "description": "x"}, {"name": "X", "description": "x"}]},
        {"callback": "not wire data"},
    ]:
        _check_case(changes)
