from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.dialogs import DialogBroker
from xdog.agent.extensions.runtime import ExtensionRuntime

FIXTURE = Path(__file__).parent / "fixtures" / "extensions"


async def test_real_worker_suspends_callback_for_multiple_dialogs_without_replay(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(["dialogue"], root, tmp_path)
    requested = []
    async def answer(name, prompt):
        requested.append(prompt)
        return {"select": "Blue", "input": "user", "confirm": True}[prompt["kind"]]
    runtime.dialog_handler = answer
    try:
        assert await runtime.command("ext-dialogue-ask", "") == "DIALOG-RESULT-Blue-user-True"
        assert [item["kind"] for item in requested] == ["select", "input", "confirm"]
        assert len({item["id"] for item in requested}) == 3
        assert runtime.snapshot()["extensions"][0]["state"] == "active"
    finally:
        await runtime.aclose()


async def test_headless_dialogs_return_canceled_values_and_timeout_does_not_disable_worker(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(["dialogue"], root, tmp_path)
    broker = DialogBroker()
    runtime.dialog_handler = broker.ask
    try:
        # No host answer: both ends share a bounded deadline and do not re-execute.
        assert await runtime.command("ext-dialogue-timeout", "") == "TIMEOUT-None"
        assert runtime.snapshot()["extensions"][0]["state"] == "active"
        runtime.dialog_handler = None
        assert await runtime.command("ext-dialogue-ask", "") == "DIALOG-RESULT-None-None-False"
    finally:
        broker.close()
        await runtime.aclose()


async def test_broker_cross_thread_resolution_close_and_stale_ids():
    broker = DialogBroker()
    notices = []
    broker.publish = notices.append
    task = asyncio.create_task(broker.ask("test", {"id": "worker", "kind": "confirm", "title": "Proceed"}))
    await asyncio.sleep(0)
    identifier = notices[0]["dialog"]["id"]
    assert identifier != "worker" and broker.contains(identifier)
    assert not broker.resolve("stale", True)
    with pytest.raises(ValueError):
        broker.resolve(identifier, "yes")
    broker.close()
    assert await task is False
    assert not broker.contains(identifier)


async def test_canceling_a_callback_waiting_for_ui_cancels_broker_and_reaps_process(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(["dialogue"], root, tmp_path)
    broker = DialogBroker()
    notices = []
    broker.publish = notices.append
    runtime.dialog_handler = broker.ask
    try:
        await runtime.initialize()
        client = runtime._manager._clients["dialogue"]
        process = client.process
        task = asyncio.create_task(runtime.command("ext-dialogue-ask", ""))
        async with asyncio.timeout(3):
            while not notices:
                await asyncio.sleep(0.005)
        key = notices[0]["dialog"]["id"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not broker.contains(key) and process.returncode is not None
        assert runtime.snapshot()["extensions"][0]["state"] == "failed"
    finally:
        broker.close()
        await runtime.aclose()


async def test_quarantine_cancels_waiting_questions_for_only_the_failed_extension():
    broker = DialogBroker()
    notices = []
    broker.publish = notices.append
    first = asyncio.create_task(broker.ask("one", {"id": "a", "kind": "input", "title": "First"}))
    second = asyncio.create_task(broker.ask("two", {"id": "b", "kind": "input", "title": "Second"}))
    await asyncio.sleep(0)
    broker.cancel_extension("one")
    assert await first is None and not second.done()
    broker.close()
    assert await second is None


@pytest.mark.parametrize("operation", ["navigate", "rename"])
@pytest.mark.parametrize("outcome", ["accept", "deny", "late", "timeout", "cancel"])
async def test_review_receipt_requires_actual_callback_value_and_precedes_closure(operation, outcome):
    broker = DialogBroker()
    events = []
    broker.publish = events.append
    request = {"id": "worker", "kind": "session", "title": "Review", "operation": operation, "timeout": 0.1}
    if operation == "navigate":
        request["target"] = {"action": "new", "session_id": "", "branch_id": "", "index": -1}
    else:
        request.update(name="Reviewed", expected_name="Original")
    task = asyncio.create_task(broker.ask("owned", request))
    await asyncio.sleep(0)
    identifier = events[0]["dialog"]["id"]
    if outcome == "late":
        # Membership/ACK still succeeds before ask's finally removes the entry.
        broker._pending[identifier][2].set_result(None)
        assert broker.resolve(identifier, True)
    elif outcome == "cancel":
        task.cancel()
    elif outcome != "timeout":
        assert await asyncio.to_thread(broker.resolve, identifier, outcome == "accept")
    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert await task is (True if outcome == "accept" else None if outcome in {"late", "timeout"} else False)
    receipts = [event for event in events if event["type"] == "extension_dialog_accepted"]
    assert receipts == ([{"type": "extension_dialog_accepted", "id": identifier,
                         "extension": "owned", "operation": operation}] if outcome == "accept" else [])
    assert events[-1] == {"type": "extension_dialog_closed", "id": identifier}
    assert not broker.has_pending
