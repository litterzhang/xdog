import asyncio

from xdog.eyes.loader import parse
from xdog.eyes.models import FileState
from xdog.eyes.sources.file import FileSnapshot


async def test_shared_source_routes_and_runtime_status(tmp_path, observer_data):
    from xdog.eyes.executor import observe
    from xdog.eyes.testing import FakeClock

    observer_data["routes"].append({"id": "second-route", "detector": "changes", "handler": "notify",
                                     "event_types": ["file.created"]})
    definition = parse(observer_data, base_dir=tmp_path)
    cancel = asyncio.Event()
    clock = FakeClock(limit=5.0)
    scans, deliveries, statuses = [], [], []

    async def scanner(source):
        scans.append(source.id)
        return FileSnapshot(()) if len(scans) == 1 else FileSnapshot((("a", FileState("new", 3)),))

    async def deliver(value):
        deliveries.append(value)
        return "accepted"

    def emit(event):
        statuses.append(event.kind)
        if statuses.count("delivery_accepted") == 2:
            cancel.set()

    await observe(definition, registration_id="registration", scanner=scanner, deliver=deliver,
                  emit=emit, clock=clock.now, sleep=clock.sleep, cancel=cancel)
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
            raise OSError("lost ACK")
        return "duplicate"

    def emit(event):
        if event.kind == "delivery_accepted":
            cancel.set()

    async def sleep(seconds):
        now[0] += seconds
        assert now[0] < 6
        await asyncio.sleep(0)

    await observe(parse(observer_data, base_dir=tmp_path), registration_id="registration", scanner=scanner,
                  deliver=deliver, emit=emit, clock=lambda: now[0], sleep=sleep, cancel=cancel)
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
        return "accepted"

    def emit(event):
        statuses.append(event.kind)

    async def sleep(seconds):
        now[0] += seconds
        if now[0] > 2.5:
            cancel.set()
        await asyncio.sleep(0)

    await observe(parse(observer_data, base_dir=tmp_path), registration_id="registration", scanner=scanner,
                  deliver=deliver, emit=emit, clock=lambda: now[0], sleep=sleep, cancel=cancel)
    assert "source_error" in statuses
    assert deliveries == []


async def test_overflow_is_visible_and_pauses_registration(tmp_path, observer_data):
    from xdog.eyes.executor import observe

    cancel = asyncio.Event()
    now, scans, deliveries, statuses = [0.0], [0], [], []

    async def scanner(source):
        scans[0] += 1
        return FileSnapshot(()) if scans[0] == 1 else FileSnapshot(
            tuple((f"{i}.py", FileState("new", 3)) for i in range(257)))

    async def deliver(value):
        deliveries.append(value)
        return "accepted"

    def emit(event):
        statuses.append(event)
        if event.kind == "paused":
            cancel.set()

    async def sleep(seconds):
        now[0] += seconds
        assert now[0] < 5
        await asyncio.sleep(0)

    await observe(parse(observer_data, base_dir=tmp_path), registration_id="registration", scanner=scanner,
                  deliver=deliver, emit=emit, clock=lambda: now[0], sleep=sleep, cancel=cancel)
    assert any(event.kind == "paused" and "overflow" in event.error for event in statuses)
    assert deliveries == []


async def test_stale_owner_stops_and_closes_inflight_work(tmp_path, observer_data):
    from xdog.eyes.executor import observe

    cancel = asyncio.Event()
    now, scans, statuses = [0.0], [0], []

    async def scanner(source):
        scans[0] += 1
        return FileSnapshot(()) if scans[0] == 1 else FileSnapshot((("a", FileState("new", 3)),))

    async def deliver(value):
        return "stale-owner"

    async def sleep(seconds):
        now[0] += seconds
        assert now[0] < 5
        await asyncio.sleep(0)

    await observe(parse(observer_data, base_dir=tmp_path), registration_id="registration", scanner=scanner,
                  deliver=deliver, emit=lambda event: statuses.append(event.kind), clock=lambda: now[0],
                  sleep=sleep, cancel=cancel)
    assert cancel.is_set()
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
        return "paused" if len(attempts) == 1 else "accepted"

    def emit(event):
        if event.kind == "delivery_accepted":
            cancel.set()

    async def sleep(seconds):
        now[0] += seconds
        if now[0] >= 3:
            resume.set()
        assert now[0] < 4
        await asyncio.sleep(0)

    await observe(parse(observer_data, base_dir=tmp_path), registration_id="registration", scanner=scanner,
                  deliver=deliver, emit=emit, clock=lambda: now[0], sleep=sleep, cancel=cancel, resume=resume)
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

    await observe(parse(observer_data, base_dir=tmp_path), registration_id="registration", scanner=scanner,
                  deliver=deliver, emit=lambda event: None, clock=lambda: now[0], sleep=sleep, cancel=cancel)
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

    await observe(parse(observer_data, base_dir=tmp_path), registration_id="registration", scanner=scanner,
                  deliver=deliver, emit=emit, clock=clock.now, sleep=clock.sleep, cancel=cancel)
    assert len(attempts) == 1
    assert any(event.kind == "delivery_failed" and "handler failed" in event.error for event in statuses)
