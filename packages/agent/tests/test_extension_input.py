from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.input import InputRejected, decision
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.ai.types import ImageContent, TextContent, UserMessage

FIXTURE = Path(__file__).parent / "fixtures/extensions"


def test_invalid_input_decisions_cannot_coerce_dispatch_drop_images_or_implicitly_handle() -> None:

    def _check_case(value):
        with pytest.raises(ValueError):
            decision(value)

    for value in [
        42,
        True,
        {"action": "unknown"},
        {"action": "transform", "text": 4},
        {"action": "reject", "reason": ""},
        {"action": "continue", "text": "must not change"},
        {"action": "transform", "text": "x", "images": []},
        {"action": "handled"},
    ]:
        _check_case(value)


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


@pytest.mark.parametrize("value", ["hook-fail"])
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
