from __future__ import annotations

from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.runtime import validate_snapshot
from xdog.agent.extensions.shortcuts import shortcut_key

FIXTURE = Path(__file__).parent / "fixtures/extensions"


def test_unsafe_or_ambiguous_shortcut_registration_cannot_take_typing_approval_exit_recovery() -> None:

    def _check_case(key):
        with pytest.raises(ValueError):
            shortcut_key(key)

    for key in [
        "plain",
        "a",
        "shift+a",
        "enter",
        "tab",
        "escape",
        "ctrl+c",
        "ctrl+d",
        "ctrl+l",
        "ctrl+z",
        "alt+alt+k",
        "ctrl+unsupported",
        "ctrl+alt+k+q",
        "\x1b[2J",
    ]:
        _check_case(key)


def test_shortcut_registers_actual_command_without_duplicate_callback_or_namespace():
    api = ExtensionAPI({})
    def callback(args, context):
        return "command"
    api.shortcut("compose", "ALT+K", "Compose draft", callback)
    assert api.commands["compose"][1] is callback and api.shortcuts[0]["key"] == "alt+k"
    with pytest.raises(ValueError):
        api.shortcut("other", "alt+k", "Conflicting local key", callback)
    with pytest.raises(ValueError):
        api.shortcut("compose", "f2", "Duplicate command", callback)


@pytest.mark.parametrize(
    "mutate", [{"key": "ctrl+c"}, {"command": "ext-other-compose"}, {"extension": "inactive"}, {"description": True}]
)
def test_invalid_remote_shortcuts_do_not_enter_ui_dispatch(mutate):
    row = {"extension": "keyed", "command": "ext-keyed-compose", "description": "Compose", "key": "alt+k"}
    with pytest.raises(ValueError):
        validate_snapshot(
            {
                "extensions": [{"name": "keyed", "state": "active"}],
                "commands": {"ext-keyed-compose": "Compose"},
                "ui": {},
                "shortcuts": [{**row, **mutate}],
            }
        )
