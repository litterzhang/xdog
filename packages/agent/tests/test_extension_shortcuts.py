from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.runtime import ExtensionRuntime, validate_snapshot
from xdog.agent.extensions.shortcuts import shortcut_key

FIXTURE = Path(__file__).parent / "fixtures/extensions"


@pytest.mark.parametrize(
    "key",
    [
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
    ],
)
def test_unsafe_or_ambiguous_shortcut_registration_cannot_take_typing_approval_exit_recovery(key):
    with pytest.raises(ValueError):
        shortcut_key(key)


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


async def test_real_worker_shortcut_snapshot_actual_command_reload_unload_and_legacy_compatibility(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(["keyed"], root, tmp_path)
    try:
        await runtime.initialize()
        value = validate_snapshot(runtime.snapshot())
        assert value["shortcuts"][0]["command"] == "ext-keyed-compose" and value["shortcuts"][0]["key"] == "alt+k"
        assert await runtime.command("ext-keyed-compose", "") == "SHORTCUT-EDITOR-UNAVAILABLE"
        await runtime.manage("unload", "keyed")
        assert not runtime.snapshot()["shortcuts"]
        await runtime.manage("reload", "keyed")
        assert len(runtime.snapshot()["shortcuts"]) == 2
    finally:
        await runtime.aclose()
    assert validate_snapshot({"extensions": [], "commands": {}, "ui": {}})


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
