from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.input import InputRejected, decision
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.ai.types import ImageContent, TextContent, UserMessage

FIXTURE = Path(__file__).parent / "fixtures/extensions"


@pytest.mark.parametrize(
    "value",
    [
        42,
        True,
        {"action": "unknown"},
        {"action": "transform", "text": 4},
        {"action": "reject", "reason": ""},
        {"action": "continue", "text": "must not change"},
        {"action": "transform", "text": "x", "images": []},
        {"action": "handled"},
    ],
)
def test_invalid_input_decisions_cannot_coerce_dispatch_drop_images_or_implicitly_handle(value):
    with pytest.raises(ValueError):
        decision(value)


async def test_real_worker_transform_preserves_original_images_timestamp_without_exposing_base64(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(["inputter"], root, tmp_path)
    image = ImageContent(data="not-shared-with-hook", mime_type="image/png")
    value = UserMessage(content=(TextContent(text="user text"), image), timestamp=123)
    try:
        transformed = await runtime.input(value, "follow_up", channel="coding")
        assert transformed.content[0].text == "INPUT-HOOK-user text" and transformed.content[1] is image
        assert transformed.timestamp == 123
        metadata = runtime.entries.for_extension("inputter")[-1]["data"]
        assert metadata == {"source": "follow_up", "channel": "coding", "image_count": 1, "has_raw_images": False}
        with pytest.raises(InputRejected, match="rejected input"):
            await runtime.input(UserMessage(content="hook-reject"))
        assert runtime.snapshot()["extensions"][0]["state"] == "active"
    finally:
        await runtime.aclose()


@pytest.mark.parametrize("value", ["hook-fail", "hook-bad"])
async def test_failed_or_malformed_hook_never_falls_back_to_original_input_or_auto_replays(tmp_path, value):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(["inputter"], root, tmp_path)
    try:
        with pytest.raises(InputRejected, match="failed"):
            await runtime.input(UserMessage(content=value))
        with pytest.raises(InputRejected, match="unavailable"):
            await runtime.input(UserMessage(content="must not pass raw"))
        await runtime.manage("unload", "inputter")
        original = UserMessage(content="explicitly unloaded")
        assert await runtime.input(original) is original
    finally:
        await runtime.aclose()


async def test_missing_wire_decision_cannot_silently_admit_original_text(tmp_path, monkeypatch):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(['inputter'], root, tmp_path)
    try:
        await runtime.initialize()
        original = runtime._rpc
        async def malformed(name, method, params):
            if method == 'xdog/input':
                return {}
            return await original(name, method, params)
        monkeypatch.setattr(runtime, '_rpc', malformed)
        with pytest.raises(InputRejected, match='failed'):
            await runtime.input(UserMessage(content='original must not pass'))
        assert runtime.snapshot()['extensions'][0]['state'] == 'failed'
    finally:
        await runtime.aclose()


async def test_failed_reload_keeps_previously_registered_input_filter_required_until_explicit_unload(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(['inputter'], root, tmp_path)
    try:
        assert (await runtime.input(UserMessage(content='initial'))).content[0].text == 'INPUT-HOOK-initial'
        (root / 'inputter/main.py').write_text('raise RuntimeError("Broken replacement module")\n')
        with pytest.raises(RuntimeError):
            await runtime.manage('reload', 'inputter')
        assert runtime.snapshot()['extensions'][0]['state'] == 'failed'
        with pytest.raises(InputRejected, match='unavailable'):
            await runtime.input(UserMessage(content='raw input must remain blocked'))
        await runtime.manage('unload', 'inputter')
        original = UserMessage(content='explicitly unloaded')
        assert await runtime.input(original) is original
    finally:
        await runtime.aclose()
