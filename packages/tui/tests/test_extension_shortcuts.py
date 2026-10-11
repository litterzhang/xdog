from __future__ import annotations

import pytest
from xdog.tui.extension_shortcuts import ExtensionShortcuts
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.prompt_keymap import PromptKeymap

ROWS = [
    {"extension": "keyed", "command": "ext-keyed-compose", "key": "alt+k", "description": "Compose"},
    {"extension": "keyed", "command": "ext-keyed-blocked", "key": "ctrl+e", "description": "Conflict"},
]


def test_builtin_and_user_editor_bindings_win_and_extension_conflicts_disable_all():
    effective = ExtensionShortcuts(ROWS, PromptKeymap())
    assert effective.command(KeyEvent("k", alt=True)) == "ext-keyed-compose"
    assert effective.command(KeyEvent("e", ctrl=True)) is None
    assert "conflicts with finish" in effective.help_text()
    assert effective.command(KeyEvent("k", alt=True, event_type=KeyEventType.REPEAT)) is None
    assert effective.command(KeyEvent("k", alt=True, event_type=KeyEventType.RELEASE)) is None
    conflicting = ExtensionShortcuts(
        [ROWS[0], {**ROWS[0], "extension": "other", "command": "ext-other-compose"}], PromptKeymap()
    )
    assert not conflicting.active and "all disabled" in conflicting.help_text()


def test_user_can_remap_unbind_and_configure_unloaded_shortcuts_without_enabling_plugins():
    keymap = PromptKeymap.from_overrides(
        {"ext-keyed-compose": "alt+x", "ext-keyed-blocked": [], "ext-unloaded-action": "f2"}
    )
    effective = ExtensionShortcuts(ROWS, keymap)
    assert effective.command(KeyEvent("x", alt=True)) == "ext-keyed-compose"
    assert effective.command(KeyEvent("k", alt=True)) is None
    assert "explicitly unbound" in effective.help_text() and "no active shortcut registration" in effective.help_text()
    assert keymap.resolve(KeyEvent("x", alt=True)) == "ext-keyed-compose"
    with pytest.raises(ValueError):
        PromptKeymap.from_overrides({"ext-keyed-compose": "ctrl+e"})


@pytest.mark.parametrize("key", ["escape", "ctrl+c", "ctrl+d", "ctrl+l", "ctrl+z", "enter", "tab"])
def test_extension_overrides_cannot_take_reserved_routes_even_after_user_unbinds_action(key):
    with pytest.raises(ValueError):
        PromptKeymap.from_overrides(
            {"interrupt": [], "exit": [], "suspend": [], "send": [], "complete": [], "ext-keyed-compose": key}
        )


def test_case_insensitive_command_overrides_cannot_silently_replace_one_another():
    with pytest.raises(ValueError, match='case-insensitive'):
        PromptKeymap.from_overrides({'ext-Keyed-compose': 'alt+x', 'ext-keyed-compose': 'alt+y'})
