from __future__ import annotations

import asyncio
import base64
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.context import decode_context
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.state import ExtensionEntries
from xdog.agent.messages import messages_to_dicts
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

FIXTURE = Path(__file__).parent / "fixtures" / "extensions"


async def test_real_entry_commit_context_hook_reload_and_large_image_round_trip(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    saved = []
    state = ExtensionEntries({}, saved.append)
    runtime = ExtensionRuntime(["contextual"], root, tmp_path, entries=state)
    try:
        await runtime.initialize()
        assert await runtime.command("ext-contextual-remember", "durable") == "ENTRY-SAVED-durable"
        assert saved[-1]["contextual"][0]["data"] == {"text": "durable"}
        # This exceeds the individual 8 MiB RPC-frame limit and must be chunked,
        # not dropped/flattened to fit a convenient text-only context test.
        image = ImageContent(data=base64.b64encode(b"x" * (7 * 1024 * 1024)).decode(), mime_type="image/png")
        original = [UserMessage(content=(TextContent(text="request"), image)),
                    AssistantMessage(content=(ThinkingContent(thinking="signed", thinking_signature="signature"),
                                              ToolCall(id="call", name="read", arguments={})), stop_reason="toolUse"),
                    ToolResultMessage(tool_call_id="call", tool_name="read", content=(TextContent(text="result"),))]
        transformed = await runtime.transform(original)
        assert messages_to_dicts(transformed[:-1]) == messages_to_dicts(original)
        assert transformed[-1].content[0].text == "EXTENSION-CONTEXT-durable"
        assert len(original) == 3 and state.snapshot() == saved[-1]
        await runtime.manage("reload", "contextual")
        assert runtime.snapshot()["ui"]["contextual:entries"]["lines"] == ["RESTORED-ENTRIES-1"]
    finally:
        await runtime.aclose()


@pytest.mark.parametrize("bad", [
    [{"role": "system", "content": [{"type": "text", "text": "override"}]}],
    [{"role": "user", "content": [{"type": "unknown", "text": "drop"}]}],
    [{"role": "assistant", "content": [{"type": "toolCall", "id": "a", "name": "read", "arguments": {}}]}],
    [{"role": "toolResult", "tool_call_id": "a", "tool_name": "read", "content": []}],
])
def test_invalid_context_roles_parts_and_tool_batches_fail_instead_of_silent_coercion(bad):
    with pytest.raises(ValueError):
        decode_context(bad, [])


def test_noop_context_preserves_original_typed_objects_and_nonserialized_fields():
    original = [UserMessage(content="original")]
    assert decode_context(messages_to_dicts(original), original) is original


async def test_invalid_hook_disables_extension_and_never_calls_provider(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    (root / "contextual" / "main.py").write_text('''
def setup(api):
    def transform(messages, context):
        return [{"role": "system", "content": [{"type": "text", "text": "override"}]}]
    api.context(transform)
''')
    runtime = ExtensionRuntime(["contextual"], root, tmp_path)
    try:
        with pytest.raises(ValueError, match="system"):
            await runtime.transform([UserMessage(content="request")])
        assert runtime.snapshot()["extensions"][0]["state"] == "failed"
        with pytest.raises(ValueError, match="validation unavailable"):
            await runtime.transform([UserMessage(content="again")])
    finally:
        await runtime.aclose()


def test_entry_state_rejects_invalid_data_and_failed_writes_before_publication():
    owner = ExtensionEntries({}, lambda value: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        owner.append("sample", [{"kind": "note", "data": {"text": "value"}}])
    assert owner.snapshot() == {}
    with pytest.raises(ValueError):
        owner.append("sample", [{"kind": "note", "data": float("nan")}])
    owner.close()
    with pytest.raises(RuntimeError, match="closed"):
        owner.append("sample", [{"kind": "note", "data": "late"}])


async def test_cancelled_entry_commit_settles_before_releasing_cross_loop_owner(tmp_path):
    import threading
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    entered, release = threading.Event(), threading.Event()
    saved = []
    def persist(value):
        entered.set()
        assert release.wait(3)
        saved.append(value)
    runtime = ExtensionRuntime(["contextual"], root, tmp_path, entries=ExtensionEntries({}, persist))
    try:
        await runtime.initialize()
        task = asyncio.create_task(runtime.command("ext-contextual-remember", "committed"))
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.005)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert saved[-1]["contextual"][0]["data"] == {"text": "committed"}
    finally:
        release.set()
        await runtime.aclose()
