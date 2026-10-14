"""Continuous readiness-based observation, separate from a finite workflow run."""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace

from xdog.eyes.detectors.file_change import FileChangeDetector
from xdog.eyes.errors import SourceError
from xdog.eyes.events import RuntimeEvent
from xdog.eyes.models import Event, ObserverDef, SourceDef
from xdog.eyes.sources.file import FileSnapshot

MAX_PENDING = 128
MAX_PATHS = 256
MAX_DELIVERY_BYTES = 1024 * 1024 - 4096


@dataclass(frozen=True)
class Delivery:
    id: str
    registration_id: str
    route_id: str
    instruction: str
    events: tuple[Event, ...]


@dataclass
class _Pending:
    delivery: Delivery
    retry_at: float = 0.0
    attempts: int = 0
    task: asyncio.Task[str] | None = None
    paused: bool = False


def _bounded(delivery: Delivery) -> bool:
    paths = {evidence.path for event in delivery.events for evidence in event.evidence}
    return (len(paths) <= MAX_PATHS
            and len(json.dumps(asdict(delivery), ensure_ascii=True, allow_nan=False).encode()) <= MAX_DELIVERY_BYTES)


async def observe(
    definition: ObserverDef,
    *, registration_id: str,
    scanner: Callable[[SourceDef], Awaitable[FileSnapshot]],
    deliver: Callable[[Delivery], Awaitable[str]],
    emit: Callable[[RuntimeEvent], None],
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
    cancel: asyncio.Event,
    resume: asyncio.Event | None = None,
) -> None:
    detectors = {item.id: FileChangeDetector(item) for item in definition.detectors}
    handlers = {item.id: item for item in definition.handlers}
    scans: dict[str, asyncio.Task[FileSnapshot]] = {}
    next_scan = {item.id: clock() for item in definition.sources}
    baselined: set[str] = set()
    pending: dict[str, _Pending] = {}
    backlog: dict[str, Delivery] = {}
    overflowed = False

    def report(kind: str, *, source: str = "", delivery: Delivery | None = None, error: str = "") -> None:
        emit(RuntimeEvent(kind, registration_id, source_id=source,
                          route_id=delivery.route_id if delivery else "",
                          delivery_id=delivery.id if delivery else "", error=error,
                          event_ids=tuple(event.id for event in delivery.events) if delivery else ()))

    def enqueue(route_id: str, instruction: str, events: tuple[Event, ...]) -> bool:
        created = Delivery(uuid.uuid4().hex, registration_id, route_id, instruction, events)
        current = pending.get(route_id)
        # Never alter a delivery after its first attempt: a lost ACK may hide acceptance.
        previous = (current.delivery if current is not None and current.attempts == 0 else backlog.get(route_id))
        candidate = replace(previous, events=previous.events + events) if previous else created
        if not _bounded(candidate) or (previous is None and len(pending) + len(backlog) >= MAX_PENDING):
            report("paused", delivery=created, error="overflow: stop and restart to establish a new baseline")
            return False
        if current is None:
            pending[route_id] = _Pending(candidate)
        elif current.attempts == 0:
            current.delivery = candidate
        else:
            backlog[route_id] = candidate
        report("delivery_pending", delivery=candidate)
        return True

    async def collect(source: SourceDef) -> FileSnapshot:
        return await scanner(source)

    async def send(value: Delivery) -> str:
        return await deliver(value)

    report("baselining")
    try:
        while not cancel.is_set():
            now = clock()
            if resume is not None and resume.is_set():
                resume.clear()
                if not overflowed:
                    for item in pending.values():
                        item.paused = False
                        item.retry_at = now
            for source in definition.sources:
                task = scans.get(source.id)
                if task is not None and task.done():
                    del scans[source.id]
                    next_scan[source.id] = now + source.poll_interval_ms / 1000
                    try:
                        snapshot = task.result()
                    except SourceError as exc:
                        report("source_error", source=source.id, error=str(exc))
                    else:
                        for detector_def in definition.detectors:
                            if detector_def.source == source.id:
                                detectors[detector_def.id].observe(snapshot, now=now)
                        if source.id not in baselined:
                            baselined.add(source.id)
                            report("watching", source=source.id)
                if not overflowed and source.id not in scans and now >= next_scan[source.id]:
                    scans[source.id] = asyncio.create_task(collect(source))
            if not overflowed:
                for detector_id, detector in detectors.items():
                    events = detector.flush(now=now)
                    if not events:
                        continue
                    for event in events:
                        emit(RuntimeEvent("event_detected", registration_id, source_id=event.source_id,
                                          event_ids=(event.id,)))
                    for route in definition.routes:
                        matching = tuple(event for event in events if event.type in route.event_types)
                        if route.detector == detector_id and matching:
                            if not enqueue(route.id, handlers[route.handler].instruction, matching):
                                overflowed = True
                                break
                    if overflowed:
                        break
            for route_id, item in list(pending.items()):
                if item.task is not None and item.task.done():
                    finished, item.task = item.task, None
                    try:
                        status = finished.result()
                    except (OSError, TimeoutError) as exc:
                        status = "busy"
                        report("delivery_retry", delivery=item.delivery, error=str(exc))
                    except Exception as exc:
                        status = "rejected"
                        report("delivery_failed", delivery=item.delivery, error=str(exc))
                    if status in {"accepted", "duplicate"}:
                        report("delivery_accepted", delivery=item.delivery)
                        del pending[route_id]
                        if route_id in backlog:
                            pending[route_id] = _Pending(backlog.pop(route_id))
                        continue
                    if status == "stale-owner":
                        cancel.set()
                        break
                    if status != "busy":
                        item.paused = True
                        report("paused", delivery=item.delivery, error=status)
                    else:
                        item.retry_at = now + min(5.0, 0.25 * 2 ** min(item.attempts - 1, 5))
                if not overflowed and not item.paused and item.task is None and now >= item.retry_at:
                    item.attempts += 1
                    item.task = asyncio.create_task(send(item.delivery))
            if not cancel.is_set():
                await sleep(0.05)
    finally:
        tasks: list[asyncio.Task[object]] = []
        for scan_task in scans.values():
            scan_task.cancel()
            tasks.append(scan_task)
        for item in pending.values():
            if item.task is not None:
                item.task.cancel()
                tasks.append(item.task)
        if tasks:
            cleanup = asyncio.gather(*tasks, return_exceptions=True)
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
        report("stopped")


class Registration:
    """Own and await one observer task; active state is not persisted."""
    def __init__(self, identifier: str, definition: ObserverDef,
                 task: asyncio.Task[None], cancel: asyncio.Event, resume: asyncio.Event) -> None:
        self.id = identifier
        self.definition = definition
        self.task = task
        self.cancel = cancel
        self.resume = resume

    async def aclose(self) -> None:
        self.cancel.set()
        if not self.task.done():
            self.task.cancel()
        cleanup = asyncio.gather(self.task, return_exceptions=True)
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
