"""Tests for agent built-in tools (current_time, bash, filesystem with grep/find)."""

import asyncio
import time

import pytest
from xdog.agent.tools import (
    create_bash_tool,
    create_filesystem_tool,
)
from xdog.ai.types import TextContent


def _text(result) -> str:
    """Extract plain text from an AgentToolResult."""
    return "\n".join(p.text for p in result.content if isinstance(p, TextContent))

# -- current_time --

# -- bash --

@pytest.mark.asyncio
async def test_bash_cwd_tracking(tmp_path):
    tool = create_bash_tool(initial_cwd=tmp_path)
    (tmp_path / "sub").mkdir()

    await tool.execute("c1", {"command": "cd sub"})
    result = await tool.execute("c2", {"command": "pwd"})
    assert "sub" in _text(result)

@pytest.mark.asyncio
async def test_bash_timeout():
    tool = create_bash_tool()
    result = await tool.execute("c1", {"command": "sleep 10", "timeout_ms": 1000})
    assert "timed out" in _text(result).lower()


@pytest.mark.asyncio
async def test_bash_observes_cancel_event():
    tool = create_bash_tool()
    cancel = asyncio.Event()

    async def trigger_cancel() -> None:
        await asyncio.sleep(0.05)
        cancel.set()

    started = time.monotonic()
    asyncio.create_task(trigger_cancel())
    with pytest.raises(asyncio.CancelledError):
        await tool.execute(
            "c1",
            {"command": "sleep 10", "timeout_ms": 30_000},
            cancel,
        )
    assert time.monotonic() - started < 2


# -- filesystem: read, write, delete --

# -- filesystem: edit --

@pytest.mark.asyncio
async def test_filesystem_edit(tmp_path):
    fpath = tmp_path / "edit.txt"
    fpath.write_text("hello world\nfoo bar\n")

    tool = create_filesystem_tool()
    await tool.execute("c1", {
        "action": "edit", "path": str(fpath),
        "old_string": "hello world", "new_string": "hello universe",
    })
    assert "hello universe" in fpath.read_text()
    assert "hello world" not in fpath.read_text()

@pytest.mark.asyncio
async def test_filesystem_edit_multi_edit(tmp_path):
    fpath = tmp_path / "multi.txt"
    fpath.write_text("alpha\nbeta\ngamma\n")

    tool = create_filesystem_tool()
    await tool.execute("c1", {
        "action": "edit", "path": str(fpath),
        "edits": [
            {"old_string": "alpha", "new_string": "ALPHA"},
            {"old_string": "gamma", "new_string": "GAMMA"},
        ],
    })
    content = fpath.read_text()
    assert "ALPHA" in content
    assert "beta" in content  # untouched
    assert "GAMMA" in content

# -- filesystem: ls --

# -- filesystem: error handling --

@pytest.mark.asyncio
async def test_filesystem_errors(tmp_path):
    tool = create_filesystem_tool()

    # Missing action
    r = await tool.execute("c1", {"action": "", "path": str(tmp_path)})
    assert "error" in _text(r).lower()

    # Relative path
    r = await tool.execute("c1", {"action": "read", "path": "relative/path"})
    assert "error" in _text(r).lower()

    # Unknown action
    r = await tool.execute("c1", {"action": "nope", "path": str(tmp_path)})
    assert "error" in _text(r).lower() or "unknown" in _text(r).lower()

# -- grep (filesystem action) --

# -- find (filesystem action) --


async def test_fast_bash_commands_do_not_lookup_reaped_process_group(tmp_path, monkeypatch):
    import os

    def already_reaped(pid):
        raise ProcessLookupError("shell already exited")

    monkeypatch.setattr(os, "getpgid", already_reaped)
    tool = create_bash_tool(initial_cwd=tmp_path)
    (tmp_path / "sub").mkdir()
    for index in range(10):
        result = await tool.execute(str(index), {"command": "printf fast"})
        assert _text(result) == "fast"
    await tool.execute("cd", {"command": "cd sub"})
    result = await tool.execute("pwd", {"command": "pwd"})
    assert str(tmp_path / "sub") in _text(result)
