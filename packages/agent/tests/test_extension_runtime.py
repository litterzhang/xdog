from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.runtime import ExtensionRuntime

FIXTURE = Path(__file__).parent / "fixtures" / "extensions"


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "extensions"
    shutil.copytree(FIXTURE, path)
    return path


async def test_owned_process_load_events_hooks_commands_ui_and_stop_without_raw_console(root, tmp_path, capsys):
    marker = tmp_path / "marker"
    runtime = ExtensionRuntime(["sample"], root, tmp_path, settings={"sample": {"marker": str(marker)}})
    events = []
    runtime.publish = events.append
    assert not marker.exists() and not hasattr(runtime, "tools")
    try:
        await asyncio.gather(runtime.initialize(), runtime.initialize())
        assert marker.read_text() == "started\n"
        assert runtime.commands() == {"ext-sample-hello": "Test extension command"}
        assert await runtime.pre("filesystem", {"value": "veto"}) is None
        assert await runtime.post("filesystem", {}, "result") == "result-POST"
        assert await runtime.command("ext-sample-hello", "input") == "EXTENSION-COMMAND-input"
        await runtime.event("turn_start", {"user_input": "test"})
        assert any("EXTENSION-TURN-START" in event.get("text", "") for event in events)
        assert runtime.snapshot()["ui"]["sample:mode"]["lines"] == ["EXTENSION-COMMAND-STATUS"]
        assert "EXTENSION-IMPORT-PRINT" not in capsys.readouterr().out
    finally:
        await runtime.aclose()
    assert marker.read_text().endswith("stopped\n")
    assert not runtime._manager._thread.is_alive()


async def test_timed_out_extension_callback_reaps_process_and_required_hook_fails_closed(root, tmp_path):
    runtime = ExtensionRuntime(["sample"], root, tmp_path, timeout=3)
    try:
        await runtime.initialize()
        client = runtime._manager._clients["sample"]
        process = client.process
        from dataclasses import replace
        client.config = replace(client.config, timeout=0.2)
        with pytest.raises(TimeoutError):
            await runtime.command("ext-sample-hello", "hang")
        assert process.returncode is not None and client.process is None
        with pytest.raises(ValueError, match="validation unavailable"):
            await runtime.pre("filesystem", {"action": "read"})
        await runtime.manage("unload", "sample")
        assert await runtime.pre("filesystem", {"action": "read"}) == {"action": "read"}
        await runtime.manage("reload", "sample")
        assert runtime.snapshot()["extensions"][0]["state"] == "active"
    finally:
        await runtime.aclose()


def test_malformed_widget_and_command_snapshots_do_not_reach_renderers():
    from xdog.agent.extensions.runtime import validate_snapshot
    for value in ({"extensions": [], "commands": [], "ui": {}},
                  {"extensions": [], "commands": {}, "ui": {"bad": {"kind": "widget", "lines": [7]}}}):
        with pytest.raises(ValueError):
            validate_snapshot(value)
