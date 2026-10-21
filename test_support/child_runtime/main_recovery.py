"""UI017: actual main-Agent queue recovery remains explicit, permissioned and non-destructive."""

from __future__ import annotations

import json

import pytest
from xdog.agent import BeforeToolCallResult
from xdog.agent.child_images import input_receipt
from xdog.agent.children import ChildSpec
from xdog.agent.input_queue import QueuedInput
from xdog.ai.types import ImageContent

from test_support.child_runtime.returns import PNG, main_tool_call, tool_metadata


def saved_owner(tmp_path, source, with_image, *, runtime_factory, child_store_type):
    image = ImageContent(data=PNG, mime_type="image/png")
    parts = (image,) if with_image else ()
    store = child_store_type.scoped(tmp_path, "parent-session", "branch-head")
    spec = ChildSpec("0123456789ab", "UNCERTAIN INITIAL TASK", images=parts if source == "initial-task" else ())
    store.begin(spec)
    if source == "crash-snapshot":
        record = store.load()[0]
        store.save({**record, "initial_state": "admitted"})
        store.save_inputs(spec.id, (QueuedInput("pending-input", "UNCERTAIN QUEUED INPUT", "follow_up", images=parts),))

    def forbidden(*args):
        pytest.fail("Inspecting/restoring/copying a saved child must never create or restart its Agent")

    owner = runtime_factory(forbidden, store.save, records=store.load())
    return owner, store, spec.id, parts


def durable_bytes(store):
    return {path.name: path.read_bytes() for path in store.root.glob("*.json")}


class TestChildMainRecovery:
    @pytest.mark.parametrize("source", ["initial-task", "crash-snapshot"])
    @pytest.mark.parametrize("with_image", [False, True])
    async def test_main_agent_explicit_recovery_returns_exact_typed_bytes_without_adoption_or_replay(
        self, tmp_path, source, with_image, *, runtime_factory, child_store_type
    ):
        owner, store, child, parts = saved_owner(
            tmp_path, source, with_image, runtime_factory=runtime_factory, child_store_type=child_store_type
        )
        before = durable_bytes(store)
        calls = []

        async def policy(context, cancel):
            calls.append(context.args)

        try:
            listed = await main_tool_call(owner, child, {"action": "list"}, hook=policy)
            snapshot = tool_metadata(listed)["queue"]
            row = next(item for item in snapshot["inputs"] if item["source"] == source)
            assert snapshot["stopped"] is True and row["uncertain"] is True
            action = "recover_task" if source == "initial-task" else "recover_images" if with_image else "recover"
            command = {"action": action, "id": row["id"], "revision": snapshot["revision"]}
            copied = await main_tool_call(owner, child, command, hook=policy)
            assert not copied.is_error
            metadata = tool_metadata(copied)
            assert metadata["queue"]["recovered"] == row and metadata["queue"]["inputs"] == snapshot["inputs"]
            assert tuple(part for part in copied.content if isinstance(part, ImageContent)) == parts
            if with_image:
                assert metadata["image_recovery"] == {"receipt": input_receipt(row["content"], parts), "image_count": 1}
                assert PNG not in copied.content[0].text
            else:
                assert "image_recovery" not in metadata
            # Lost/canceled caller receipts do not remove the only durable copy. An
            # explicit repeated read is still just a copy, never a child/queue replay.
            repeated = await main_tool_call(owner, child, command, hook=policy)
            assert repeated.content == copied.content and not owner.active
            assert durable_bytes(store) == before and owner._children[child].agent is None
            assert len(calls) == 3 and all(call["action"] == "queue" for call in calls)
            assert all(set(call) == {"action", "id", "text"} for call in calls)
            assert owner._tool_return_owner not in json.dumps(owner.snapshot()) + copied.content[0].text
        finally:
            await owner.aclose()

    async def test_main_agent_stopped_recovery_is_denied_before_image_copy_or_storage_work(
        self, tmp_path, monkeypatch, *, runtime_factory, child_store_type
    ):
        owner, store, child, _ = saved_owner(
            tmp_path, "initial-task", True, runtime_factory=runtime_factory, child_store_type=child_store_type
        )
        before = durable_bytes(store)
        seen = []

        async def deny(context, cancel):
            seen.append(context.args)
            return BeforeToolCallResult(block=True, reason="RECOVERY NOT APPROVED")

        async def forbidden(*args):
            pytest.fail("Denied recovery must not decode/copy image bytes")

        try:
            snapshot = tool_metadata(await main_tool_call(owner, child, {"action": "list"}))["queue"]
            row = snapshot["inputs"][0]
            monkeypatch.setattr("xdog.agent.child_transfer.prepare_recovery", forbidden)
            result = await main_tool_call(
                owner, child, {"action": "recover_task", "id": row["id"], "revision": snapshot["revision"]}, hook=deny
            )
            assert result.is_error and "RECOVERY NOT APPROVED" in result.content[0].text
            assert len(seen) == 1 and durable_bytes(store) == before and not owner.active
        finally:
            await owner.aclose()

    @pytest.mark.parametrize("fault", ["revision", "supervisor"])
    async def test_main_agent_stopped_recovery_refuses_stale_or_foreign_supervisor_without_changing_saved_input(
        self, tmp_path, fault, *, runtime_factory, child_store_type
    ):
        owner, store, child, _ = saved_owner(
            tmp_path, "initial-task", True, runtime_factory=runtime_factory, child_store_type=child_store_type
        )
        other = runtime_factory(lambda *args: pytest.fail("Foreign recovery must not start an Agent"))
        before = durable_bytes(store)
        try:
            snapshot = tool_metadata(await main_tool_call(owner, child, {"action": "list"}))["queue"]
            row = snapshot["inputs"][0]
            result = await main_tool_call(
                other if fault == "supervisor" else owner,
                child,
                {
                    "action": "recover_task",
                    "id": row["id"],
                    "revision": "0" * 64 if fault == "revision" else snapshot["revision"],
                },
            )
            assert result.is_error and not any(isinstance(part, ImageContent) for part in result.content)
            assert durable_bytes(store) == before and not owner.active and not other.active
        finally:
            await owner.aclose()
            await other.aclose()
