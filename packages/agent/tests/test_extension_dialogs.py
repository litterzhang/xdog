from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from xdog.agent.extensions.dialogs import DialogBroker

FIXTURE = Path(__file__).parent / "fixtures" / "extensions"


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
