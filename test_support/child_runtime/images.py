"""Typed child input, bounded persistence and duplicate-queue serialization budgets."""

from __future__ import annotations

import asyncio
import base64
import threading

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, ImageContent, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=")


def picture(data=PNG):
    return ImageContent(data=base64.b64encode(data).decode(), mime_type="image/png")


class TestChildImages:
    @pytest.mark.parametrize("images", [[], (object(),), (ImageContent(data="not-base64"),), (picture(),) * 5])
    async def test_invalid_child_images_fail_before_factory_or_record_creation(
        self, tmp_path, images, *, runtime_factory
    ):
        called = []
        owner = runtime_factory(lambda *args: called.append(args))
        try:
            with pytest.raises(ValueError):
                await owner.start("task", images=images)
            assert not called and not owner.snapshot()["children"]
        finally:
            await owner.aclose()

    async def test_repeated_identical_queue_images_are_bounded_before_admission_and_remain_durable(
        self, tmp_path, monkeypatch, *, runtime_factory, child_store_type
    ):
        import xdog.agent.supervision_images as module

        body = PNG + b"\0" * 500
        monkeypatch.setattr(module, "MAX_CHILD_IMAGE_BYTES", len(body) * 3)
        entered = threading.Event()

        def stream(*args):
            async def events():
                entered.set()
                yield StartEvent(partial=AssistantMessage(content=(TextContent(text="HELD"),)))
                await asyncio.sleep(60)

            return EventStream.from_async_generator(events())

        store = child_store_type(tmp_path / "records")
        owner = runtime_factory(
            lambda spec, publish: Agent(stream, config=AgentConfig(model="offline/model")),
            store.save,
            begin=store.begin,
        )
        try:
            child = await owner.start("", images=(picture(),))
            assert await asyncio.to_thread(entered.wait, 3)
            for _ in range(3):
                result = await owner.control("steer", child, "", images=(picture(body),))
                assert result["accepted_input"]["content"] == ""
            with pytest.raises(ValueError, match="image budget"):
                await owner.control("follow-up", child, "overflow", images=(picture(body),))
            assert len(owner._children[child].agent.pending_inputs()) == 3
            await owner.control("cancel", child)
            rows = store.load()[0]["undelivered_inputs"]
            assert len(rows) == 3 and all(base64.b64decode(row["images"][0]["data"]) == body for row in rows)
            restored = runtime_factory(
                lambda *args: pytest.fail("Recovery must not construct an agent"), records=store.load()
            )
            try:
                assert not restored.active and restored.snapshot()["children"][0]["status"] == "canceled"
            finally:
                await restored.aclose()
        finally:
            await owner.aclose()
