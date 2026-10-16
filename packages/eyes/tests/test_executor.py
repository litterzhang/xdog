import asyncio

import pytest
from xdog.eyes.events import DeliveryResult
from xdog.eyes.loader import parse
from xdog.eyes.models import FileState
from xdog.eyes.sources.file import FileSnapshot


async def test_shared_source_routes_and_runtime_status(tmp_path, observer_data):
    from xdog.eyes.executor import observe
    from xdog.eyes.testing import FakeClock

    observer_data["routes"].append(
        {"id": "second-route", "detector": "changes", "handler": "notify", "event_types": ["file.created"]}
    )
    definition = parse(observer_data, base_dir=tmp_path)
    cancel = asyncio.Event()
    clock = FakeClock(limit=5.0)
    scans, deliveries, statuses = [], [], []

    async def scanner(source):
        scans.append(source.id)
        return FileSnapshot(()) if len(scans) == 1 else FileSnapshot((("a", FileState("new", 3)),))

    async def deliver(value):
        deliveries.append(value)
        return DeliveryResult("succeeded")

    def emit(event):
        statuses.append(event.kind)
        if statuses.count("delivery_succeeded") == 2:
            cancel.set()

    await observe(
        definition,
        registration_id="registration",
        scanner=scanner,
        deliver=deliver,
        emit=emit,
        clock=clock.now,
        sleep=clock.sleep,
        cancel=cancel,
    )
    assert scans == ["files", "files"]
    assert {item.route_id for item in deliveries} == {"review", "second-route"}
    assert len({item.id for item in deliveries}) == 2
    assert "baselining" in statuses and "watching" in statuses
    assert statuses[-1] == "stopped"


async def test_retries_keep_identity_and_backoff(tmp_path, observer_data):
    from xdog.eyes.executor import observe

    cancel = asyncio.Event()
    now, attempts, scans = [0.0], [], [0]

    async def scanner(source):
        scans[0] += 1
        return FileSnapshot(()) if scans[0] == 1 else FileSnapshot((("a", FileState("new", 3)),))

    async def deliver(value):
        attempts.append((value.id, now[0]))
        if len(attempts) == 1:
            return DeliveryResult("not-sent", "connection unavailable before write")
        return DeliveryResult("succeeded")

    def emit(event):
        if event.kind == "delivery_succeeded":
            cancel.set()

    async def sleep(seconds):
        now[0] += seconds
        assert now[0] < 6
        await asyncio.sleep(0)

    await observe(
        parse(observer_data, base_dir=tmp_path),
        registration_id="registration",
        scanner=scanner,
        deliver=deliver,
        emit=emit,
        clock=lambda: now[0],
        sleep=sleep,
        cancel=cancel,
    )
    assert len(attempts) == 2
    assert attempts[0][0] == attempts[1][0]
    assert attempts[1][1] - attempts[0][1] >= 0.25


async def test_failed_scan_does_not_create_deletions(tmp_path, observer_data):
    from xdog.eyes.errors import SourceError
    from xdog.eyes.executor import observe

    cancel = asyncio.Event()
    now, scans, deliveries, statuses = [0.0], [0], [], []

    async def scanner(source):
        scans[0] += 1
        if scans[0] == 2:
            raise SourceError("root inaccessible")
        return FileSnapshot((("a", FileState("old", 3)),))

    async def deliver(value):
        deliveries.append(value)
        return DeliveryResult("succeeded")

    def emit(event):
        statuses.append(event.kind)

    async def sleep(seconds):
        now[0] += seconds
        if now[0] > 2.5:
            cancel.set()
        await asyncio.sleep(0)

    await observe(
        parse(observer_data, base_dir=tmp_path),
        registration_id="registration",
        scanner=scanner,
        deliver=deliver,
        emit=emit,
        clock=lambda: now[0],
        sleep=sleep,
        cancel=cancel,
    )
    assert "source_error" in statuses
    assert deliveries == []


async def test_overflow_is_visible_and_pauses_registration(tmp_path, observer_data):
    from xdog.eyes.executor import observe

    cancel = asyncio.Event()
    now, scans, deliveries, statuses = [0.0], [0], [], []

    async def scanner(source):
        scans[0] += 1
        return (
            FileSnapshot(())
            if scans[0] == 1
            else FileSnapshot(tuple((f"{i}.py", FileState("new", 3)) for i in range(257)))
        )

    async def deliver(value):
        deliveries.append(value)
        return DeliveryResult("succeeded")

    def emit(event):
        statuses.append(event)
        if event.kind == "paused":
            cancel.set()

    async def sleep(seconds):
        now[0] += seconds
        assert now[0] < 5
        await asyncio.sleep(0)

    await observe(
        parse(observer_data, base_dir=tmp_path),
        registration_id="registration",
        scanner=scanner,
        deliver=deliver,
        emit=emit,
        clock=lambda: now[0],
        sleep=sleep,
        cancel=cancel,
    )
    assert any(event.kind == "paused" and "overflow" in event.error for event in statuses)
    assert deliveries == []


async def test_retired_owner_pauses_and_closes_inflight_work(tmp_path, observer_data):
    from xdog.eyes.executor import observe

    cancel = asyncio.Event()
    now, scans, statuses = [0.0], [0], []

    async def scanner(source):
        scans[0] += 1
        return FileSnapshot(()) if scans[0] == 1 else FileSnapshot((("a", FileState("new", 3)),))

    async def deliver(value):
        return DeliveryResult("cancelled", "owner retired; stop/rebaseline")

    async def sleep(seconds):
        now[0] += seconds
        assert now[0] < 5
        await asyncio.sleep(0)

    def emit(event):
        statuses.append(event.kind)
        if event.kind == "paused":
            cancel.set()

    await observe(
        parse(observer_data, base_dir=tmp_path),
        registration_id="registration",
        scanner=scanner,
        deliver=deliver,
        emit=emit,
        clock=lambda: now[0],
        sleep=sleep,
        cancel=cancel,
    )
    assert "delivery_cancelled" in statuses and cancel.is_set()
    assert statuses[-1] == "stopped"


async def test_paused_delivery_waits_for_explicit_resume(tmp_path, observer_data):
    from xdog.eyes.executor import observe

    cancel, resume = asyncio.Event(), asyncio.Event()
    now, scans, attempts = [0.0], [0], []

    async def scanner(source):
        scans[0] += 1
        return FileSnapshot(()) if scans[0] == 1 else FileSnapshot((("a", FileState("new", 3)),))

    async def deliver(value):
        attempts.append((value.id, now[0]))
        return DeliveryResult("paused" if len(attempts) == 1 else "succeeded")

    def emit(event):
        if event.kind == "delivery_succeeded":
            cancel.set()

    async def sleep(seconds):
        now[0] += seconds
        if now[0] >= 3:
            resume.set()
        assert now[0] < 4
        await asyncio.sleep(0)

    await observe(
        parse(observer_data, base_dir=tmp_path),
        registration_id="registration",
        scanner=scanner,
        deliver=deliver,
        emit=emit,
        clock=lambda: now[0],
        sleep=sleep,
        cancel=cancel,
        resume=resume,
    )
    assert len(attempts) == 2
    assert attempts[0][0] == attempts[1][0]
    assert attempts[1][1] >= 3


async def test_cancel_settles_active_delivery(tmp_path, observer_data):
    from xdog.eyes.executor import observe

    cancel = asyncio.Event()
    now, scans, settled, started = [0.0], [0], [], []

    async def scanner(source):
        scans[0] += 1
        return FileSnapshot(()) if scans[0] == 1 else FileSnapshot((("a", FileState("new", 3)),))

    async def deliver(value):
        started.append(value.id)
        try:
            await asyncio.Event().wait()
        finally:
            settled.append(value.id)

    async def sleep(seconds):
        now[0] += seconds
        if started and scans[0] >= 3:
            cancel.set()
        assert now[0] < 4
        await asyncio.sleep(0)

    await observe(
        parse(observer_data, base_dir=tmp_path),
        registration_id="registration",
        scanner=scanner,
        deliver=deliver,
        emit=lambda event: None,
        clock=lambda: now[0],
        sleep=sleep,
        cancel=cancel,
    )
    assert len(started) == 1
    assert settled == started
    assert scans[0] >= 3


async def test_unexpected_handler_failure_is_visible_and_not_retried(tmp_path, observer_data):
    from xdog.eyes.executor import observe
    from xdog.eyes.testing import FakeClock

    cancel, clock = asyncio.Event(), FakeClock(limit=5)
    scans, attempts, statuses = [0], [], []

    async def scanner(source):
        scans[0] += 1
        return FileSnapshot(()) if scans[0] == 1 else FileSnapshot((("a", FileState("new", 3)),))

    async def deliver(value):
        attempts.append(value.id)
        raise RuntimeError("handler failed")

    def emit(event):
        statuses.append(event)
        if event.kind == "paused":
            cancel.set()

    await observe(
        parse(observer_data, base_dir=tmp_path),
        registration_id="registration",
        scanner=scanner,
        deliver=deliver,
        emit=emit,
        clock=clock.now,
        sleep=clock.sleep,
        cancel=cancel,
    )
    assert len(attempts) == 1
    assert any(event.kind == "delivery_outcome_unknown" and "handler failed" in event.error for event in statuses)


async def test_source_health_recovers_without_a_false_change(tmp_path, observer_data):
    from xdog.eyes.errors import SourceError
    from xdog.eyes.executor import observe
    from xdog.eyes.testing import FakeClock

    cancel, clock, calls, statuses = asyncio.Event(), FakeClock(limit=5), [0], []

    async def scanner(source):
        calls[0] += 1
        if calls[0] == 2:
            raise SourceError("temporary read error")
        return FileSnapshot((("a", FileState("same", 3)),))

    async def deliver(value):
        raise AssertionError("A recovered identical snapshot is not an event")

    def emit(event):
        statuses.append(event.kind)
        if calls[0] >= 3 and event.kind == "watching":
            cancel.set()

    await observe(
        parse(observer_data, base_dir=tmp_path),
        registration_id="registration",
        scanner=scanner,
        deliver=deliver,
        emit=emit,
        clock=clock.now,
        sleep=clock.sleep,
        cancel=cancel,
    )
    assert statuses.count("watching") == 2
    assert "source_error" in statuses


@pytest.mark.parametrize('mode,attempts', [('prompt',1),('enqueue',2)])
async def test_unknown_receipt_retries_only_an_explicitly_resumed_durable_route(tmp_path,observer_data,mode,attempts):
    from xdog.eyes.executor import observe
    from xdog.eyes.testing import FakeClock

    observer_data['handlers'][0]['delivery']=mode
    definition = parse(observer_data,base_dir=tmp_path)
    clock,cancel,resume = FakeClock(limit=6),asyncio.Event(),asyncio.Event()
    sent,events = [],[]
    scanned = []
    resumed = False

    async def scan(source):
        scanned.append(clock.now())
        return FileSnapshot(()) if len(scanned)==1 else FileSnapshot((('a',FileState('new',3)),))

    async def send(item):
        sent.append(item)
        return (DeliveryResult('outcome-unknown','lost',retry_on_resume=mode == 'enqueue')
                if len(sent)==1 else DeliveryResult('queued'))

    def emit(event):
        events.append(event)
        if event.kind=='delivery_queued':
            cancel.set()

    async def sleep(seconds):
        nonlocal resumed
        if any(e.kind=='paused' for e in events) and not resumed:
            resume.set()  # Explicit operator action; production handler also validates/reconnects its target.
            resumed = True
        if mode=='prompt' and resumed and clock.now()>3:
            cancel.set()
        await clock.sleep(seconds)

    await observe(definition,registration_id='r',scanner=scan,deliver=send,emit=emit,
                  clock=clock.now,sleep=sleep,cancel=cancel,resume=resume,cwd=tmp_path)
    assert len(sent)==attempts
    if attempts==2:
        assert sent[0]==sent[1] and sent[0].id==sent[1].id
    assert not any(e.kind=='delivery_succeeded' for e in events)
