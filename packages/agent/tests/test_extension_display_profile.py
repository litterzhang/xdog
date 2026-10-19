"""Declared display-only failures cannot become validation authority or veto it."""
from __future__ import annotations

import pytest
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.worker import manifest


def extension(root, name, body, *, display=False):
    directory = root / name
    directory.mkdir()
    (directory / "extension.yaml").write_text(f"name: {name}\ndisplay_only: {str(display).lower()}\n")
    (directory / "main.py").write_text(body)
    return directory


async def test_failed_display_is_nonblocking_but_the_real_required_guard_still_blocks(tmp_path):
    extension(tmp_path, "display", 'def setup(api):\n    api.command("ping", "Read", lambda *a: "ok")\n', display=True)
    extension(tmp_path, "guard", '''def setup(api):
    api.before_tool(lambda name, args, context: None)
''')
    runtime = ExtensionRuntime(("display", "guard"), tmp_path, tmp_path)
    try:
        await runtime.initialize()
        runtime.fail("display")
        assert await runtime.transform([]) == []
        assert await runtime.pre("filesystem", {"action": "write"}) is None
        runtime.fail("guard")
        with pytest.raises(ValueError, match="validation unavailable"):
            await runtime.pre("filesystem", {"action": "read"})
    finally:
        await runtime.aclose()


@pytest.mark.parametrize("body", [
    'def setup(api):\n    api.before_tool(lambda *a: None)\n',
    'def setup(api):\n    api.after_tool(lambda *a: "changed")\n',
    'def setup(api):\n    api.context(lambda *a: [])\n',
    'def setup(api):\n    api.on_input(lambda *a: None)\n',
    'def setup(api):\n    api.append_entry("escape", {"history": "must not persist"})\n',
    'def setup(api):\n    api.register_provider("local", '
    '[{"id":"model", "context_window":32000, "max_tokens":2048}], lambda *a: None)\n',
    'def setup(api):\n    api.on("turn_start", lambda *a: None)\n',
    'def setup(api):\n    api.tool("escape", "Not a display", {"type":"object"}, lambda *a: api.text("bad"))\n',
])
async def test_display_cannot_register_critical_hooks_events_or_tools(tmp_path, body):
    extension(tmp_path, "display", body, display=True)
    runtime = ExtensionRuntime(("display",), tmp_path, tmp_path)
    try:
        await runtime.initialize()
        assert runtime.snapshot()["extensions"] == [{"name": "display", "state": "failed"}]
        assert not runtime.tools() and runtime.entries.snapshot() == {}
        assert await runtime.pre("filesystem", {"action": "read"}) == {"action": "read"}
    finally:
        await runtime.aclose()


def test_display_profile_is_a_strict_declaration_not_truthy_coercion(tmp_path):
    directory = extension(tmp_path, "display", "", display=True)
    (directory / "extension.yaml").write_text("name: display\ndisplay_only: yes-please\n")
    with pytest.raises(ValueError, match="display"):
        manifest(directory)


async def test_missing_display_entry_does_not_turn_a_known_display_into_a_required_guard(tmp_path):
    path = extension(tmp_path, "display", "", display=True)
    (path / "main.py").unlink()
    runtime = ExtensionRuntime(("display",), tmp_path, tmp_path)
    try:
        await runtime.initialize()
        assert runtime.snapshot()["extensions"] == [{"name": "display", "state": "failed"}]
        assert await runtime.transform([]) == []
        assert await runtime.pre("filesystem", {"action": "read"}) == {"action": "read"}
    finally:
        await runtime.aclose()
