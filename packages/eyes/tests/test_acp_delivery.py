"""A prompt's unknown outcome is never confused with a safe pre-send retry."""

import asyncio
from dataclasses import asdict

import pytest
from xdog.eyes.events import DeliveryResult
from xdog.eyes.loader import parse
from xdog.eyes.models import FileState
from xdog.eyes.sources.file import FileSnapshot
from xdog.eyes.testing import FakeClock


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "outcome-unknown", "oserror", "timeout", "task-cancelled"])
async def test_terminal_or_ambiguous_outcome_never_retries_even_after_resume(tmp_path, observer_data, outcome):
    from xdog.eyes.executor import observe

    clock, cancel, resume = FakeClock(limit=8), asyncio.Event(), asyncio.Event()
    scans, attempts, events = [], [], []
    paused_at = None

    async def scanner(source):
        scans.append(clock.now())
        return FileSnapshot(()) if len(scans) == 1 else FileSnapshot((("a", FileState("changed", 3)),))

    async def deliver(value):
        attempts.append(value.id)
        if outcome == "oserror":
            raise OSError("transport result lost")
        if outcome == "timeout":
            raise TimeoutError("result timed out")
        if outcome == "task-cancelled":
            raise asyncio.CancelledError
        return DeliveryResult(outcome, "inspect effects; stop/rebaseline")

    def emit(event):
        nonlocal paused_at
        events.append(event)
        if event.kind == "paused" and paused_at is None:
            paused_at = (clock.now(), len(scans))

    async def sleep(seconds):
        await clock.sleep(seconds)
        if paused_at is not None:
            resume.set()  # Must not replay a sent/failed/uncertain prompt.
            if clock.now() - paused_at[0] > 3:
                cancel.set()

    await observe(
        parse(observer_data, base_dir=tmp_path),
        registration_id="reg",
        scanner=scanner,
        deliver=deliver,
        emit=emit,
        clock=clock.now,
        sleep=sleep,
        cancel=cancel,
        resume=resume,
    )
    assert len(attempts) == 1
    assert paused_at is not None and len(scans) == paused_at[1]
    assert not any(event.kind == "delivery_succeeded" for event in events)
    assert any("stop" in event.error.lower() for event in events if event.kind == "paused")


async def test_real_sdk_disconnect_records_one_request_and_freezes_source_until_rebaseline(tmp_path, observer_data):
    from xdog.eyes.executor import observe
    from xdog.eyes.handlers.acp import AcpHandler

    from packages.eyes.tests.test_acp_handler import definition, rows

    record = tmp_path / "peer.jsonl"
    target = definition(record, mode="disconnect")
    observer_data["handlers"][0].update(
        connection={"transport": "stdio", "command": list(target.connection.command)}, session=target.session
    )
    config = parse(observer_data, base_dir=tmp_path)
    handler = AcpHandler(config.handlers[0], cwd=tmp_path)
    clock, cancel, resume = FakeClock(limit=8), asyncio.Event(), asyncio.Event()
    attempts, events, scans = [], [], []
    paused_at = None

    async def scanner(source):
        scans.append(clock.now())
        return FileSnapshot(()) if len(scans) == 1 else FileSnapshot((("a", FileState("changed", 3)),))

    async def deliver(value):
        attempts.append(value.id)
        return await handler.deliver(value)

    def emit(event):
        nonlocal paused_at
        events.append(event)
        if event.kind == "paused" and paused_at is None:
            paused_at = clock.now(), len(scans)

    async def sleep(seconds):
        await clock.sleep(seconds)
        await asyncio.sleep(0.01)
        if paused_at is not None:
            resume.set()
            if clock.now() - paused_at[0] > 2:
                cancel.set()

    try:
        await handler.start()
        await observe(
            config,
            registration_id="reg",
            scanner=scanner,
            deliver=deliver,
            emit=emit,
            clock=clock.now,
            sleep=sleep,
            cancel=cancel,
            resume=resume,
        )
        assert len(attempts) == 1
        assert len([item for item in rows(record) if item["kind"] == "prompt"]) == 1
        assert any(item.kind == "delivery_outcome_unknown" for item in events)
        assert len(scans) == paused_at[1]
    finally:
        await handler.close()


async def test_first_attempt_identity_and_evidence_are_frozen_when_later_changes_arrive(tmp_path, observer_data):
    from xdog.eyes.executor import observe

    clock, cancel = FakeClock(limit=8), asyncio.Event()
    attempts, scans, successes = [], [], []
    release = asyncio.Event()

    async def scanner(source):
        scans.append(clock.now())
        digest = "first" if len(scans) == 2 else "second"
        return FileSnapshot(()) if len(scans) == 1 else FileSnapshot((("a", FileState(digest, 3)),))

    async def deliver(value):
        attempts.append(asdict(value))
        if len(attempts) == 1:
            await release.wait()
            return DeliveryResult("not-sent", "failed locally before write")
        return DeliveryResult("succeeded")

    def emit(event):
        if event.kind == "delivery_succeeded":
            successes.append(event)
            if len(successes) == 2:
                cancel.set()

    async def sleep(seconds):
        await clock.sleep(seconds)
        if clock.now() >= 3.2:
            release.set()

    await observe(
        parse(observer_data, base_dir=tmp_path),
        registration_id="reg",
        scanner=scanner,
        deliver=deliver,
        emit=emit,
        clock=clock.now,
        sleep=sleep,
        cancel=cancel,
    )
    assert len(attempts) == 3
    assert attempts[0] == attempts[1]
    assert attempts[0]["id"] != attempts[2]["id"]
    assert attempts[0]["events"][0]["evidence"][0]["after"]["digest"] == "first"
    assert attempts[2]["events"][0]["evidence"][0]["after"]["digest"] == "second"


async def test_wire_capacity_counts_both_text_and_structured_evidence_without_truncating(tmp_path, observer_data):
    from xdog.eyes.executor import observe
    from xdog.eyes.handlers.acp import AcpHandler

    config = parse(observer_data, base_dir=tmp_path)
    handler = AcpHandler(config.handlers[0], cwd=tmp_path)

    clock, cancel, scans, attempted, events = FakeClock(limit=5), asyncio.Event(), [], [], []

    async def scanner(source):
        scans.append(True)
        return (
            FileSnapshot(())
            if len(scans) == 1
            else FileSnapshot(tuple((f"{i}/" + "/".join(["x" * 100] * 19), FileState("hash", 3)) for i in range(256)))
        )

    async def deliver(value):
        attempted.append(value)
        return DeliveryResult("succeeded")

    def emit(event):
        events.append(event)
        if event.kind == "paused":
            cancel.set()

    await observe(
        config,
        registration_id="reg",
        scanner=scanner,
        deliver=deliver,
        emit=emit,
        clock=clock.now,
        sleep=clock.sleep,
        cancel=cancel,
        validate=handler.validate,
    )
    assert not attempted
    assert any(item.kind == "paused" and "overflow" in item.error for item in events)


async def test_pending_capacity_stops_before_sending_a_partial_route_frontier(tmp_path, observer_data):
    from xdog.eyes.executor import observe

    observer_data["routes"] = [
        {"id": f"route-{i}", "detector": "changes", "handler": "notify", "event_types": ["file.created"]}
        for i in range(129)
    ]
    clock, cancel, scans, events, attempted = FakeClock(limit=5), asyncio.Event(), [], [], []

    async def scanner(source):
        scans.append(True)
        return FileSnapshot(()) if len(scans) == 1 else FileSnapshot((("a", FileState("hash", 3)),))

    async def deliver(value):
        attempted.append(value)
        return DeliveryResult("succeeded")

    def emit(event):
        events.append(event)
        if event.kind == "paused":
            cancel.set()

    await observe(
        parse(observer_data, base_dir=tmp_path),
        registration_id="reg",
        scanner=scanner,
        deliver=deliver,
        emit=emit,
        clock=clock.now,
        sleep=clock.sleep,
        cancel=cancel,
    )
    assert not attempted
    assert len([event for event in events if event.kind == "delivery_pending"]) == 128
    assert any(event.kind == "paused" and "overflow" in event.error for event in events)
