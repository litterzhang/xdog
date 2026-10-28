"""Continuous readiness-based observation, separate from a finite workflow run."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

from xdog.eyes.checkpoint import data, delivery_data, restore_delivery
from xdog.eyes.deliveries import Delivery
from xdog.eyes.detectors.file_change import FileChangeDetector
from xdog.eyes.detectors.records import RecordDetector
from xdog.eyes.detectors.scheduler_due import SchedulerDueDetector
from xdog.eyes.errors import SourceError
from xdog.eyes.events import DeliveryResult, RuntimeEvent
from xdog.eyes.models import (
    AvailabilityDetectorDef,
    ConditionDetectorDef,
    DetectorDef,
    Event,
    FileEvidence,
    GithubSourceDef,
    HttpSourceDef,
    ObserverDef,
    RecordDetectorDef,
    RemoteSourceDef,
    SchedulerDetectorDef,
    SourceDef,
)
from xdog.eyes.router import EventRouter
from xdog.eyes.sources.file import FileSnapshot
from xdog.eyes.sources.records import PollResult
from xdog.eyes.sources.remote import RemoteSampler
from xdog.eyes.sources.scheduler import SchedulerSource, prepare_schedulers

MAX_PENDING = 128
MAX_PATHS = 256
MAX_DELIVERY_BYTES = 1024 * 1024 - 4096


@dataclass
class _Pending:
    delivery: Delivery
    retry_at: float = 0.0
    attempts: int = 0
    task: asyncio.Task[DeliveryResult] | None = None
    paused: bool = False
    terminal: bool = False
    inflight: bool = False


def _bounded(delivery: Delivery) -> bool:
    paths = {(evidence.root, evidence.path) for event in delivery.events for evidence in event.evidence
             if isinstance(evidence, FileEvidence)}
    occurrences = sum(not isinstance(evidence, FileEvidence)
                      for event in delivery.events for evidence in event.evidence)
    try:
        # Generic safety bound; protocol encodings are checked by their handler.
        encoded = json.dumps(asdict(delivery), ensure_ascii=True, allow_nan=False).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return False
    return len(paths) + occurrences <= MAX_PATHS and len(encoded) <= MAX_DELIVERY_BYTES


async def observe(
    definition: ObserverDef,
    *,
    registration_id: str,
    scanner: Callable[[SourceDef], Awaitable[FileSnapshot]],
    deliver: Callable[[Delivery], Awaitable[DeliveryResult]],
    emit: Callable[[RuntimeEvent], None],
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
    cancel: asyncio.Event,
    resume: asyncio.Event | None = None,
    suspended: asyncio.Event | None = None,
    validate: Callable[[Delivery], None] | None = None,
    wall_clock: Callable[[], float] = time.time,
    scheduler_sources: dict[str, SchedulerSource] | None = None,
    checkpoint: dict[str, Any] | None = None,
    save: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    replay_safe: bool = False,
    remote_sampler: RemoteSampler | None = None,
) -> None:
    files = {item.id: item for item in definition.sources if isinstance(item, SourceDef)}
    remotes = {item.id: item for item in definition.sources if isinstance(item, (HttpSourceDef, GithubSourceDef))}
    sources: dict[str, SourceDef | RemoteSourceDef] = {**files, **remotes}
    remote = remote_sampler if remote_sampler is not None else RemoteSampler()
    remote_state: dict[str, dict[str, Any]] = (checkpoint or {}).get('remote_sources', {})
    detectors: dict[str, FileChangeDetector | RecordDetector] = {
        item.id: FileChangeDetector(item, source_root=str(files[item.source].root))
        for item in definition.detectors if isinstance(item, DetectorDef)
    }
    detectors.update({item.id: RecordDetector(item, max_records=remotes[item.source].max_records)
        for item in definition.detectors
        if isinstance(item, (RecordDetectorDef, ConditionDetectorDef, AvailabilityDetectorDef))})
    timed_detectors = {item.id: SchedulerDueDetector(item) for item in definition.detectors
                       if isinstance(item, SchedulerDetectorDef)}
    schedulers = ({} if checkpoint is not None else
                  prepare_schedulers(definition, wall_now=wall_clock(), monotonic_now=clock())
                  if scheduler_sources is None else scheduler_sources)
    if checkpoint is not None:
        from xdog.eyes.models import SchedulerSourceDef
        schedulers = {item.id: SchedulerSource.restore(item, checkpoint['schedulers'][item.id],
                      wall_now=wall_clock(), monotonic_now=clock())
                      for item in definition.sources if isinstance(item, SchedulerSourceDef)}
    active_detectors: tuple[FileChangeDetector | RecordDetector | SchedulerDueDetector, ...] = (
        *detectors.values(), *timed_detectors.values())
    router = EventRouter(definition)
    detector_sources = {item.id: item.source for item in definition.detectors}
    route_sources = {item.id: detector_sources[item.detector] for item in definition.routes}
    scans: dict[str, asyncio.Task[FileSnapshot | PollResult]] = {}
    next_scan = {item.id: clock() + max(0.0, remote_state.get(item.id, {}).get('next_poll_at', wall_clock())
                                      - wall_clock()) for item in sources.values()}
    baselined: set[str] = set()
    source_errors: set[str] = set()
    pending: dict[str, _Pending] = {}
    backlog: dict[str, Delivery] = {}
    overflowed = False

    if checkpoint is not None:
        for key, value in checkpoint['detectors'].items():
            detectors[key].restore(value, now=clock())
            if value.get('baseline') is not None or value.get('records') is not None:
                baselined.add(detectors[key].definition.source)
        for key, value in checkpoint['pending'].items():
            ambiguous = value['inflight'] and not replay_safe
            pending[key] = _Pending(restore_delivery(value['delivery'], definition, registration_id),
                attempts=value['attempts'], paused=value['paused'] or ambiguous,
                terminal=value['terminal'] or ambiguous, inflight=value['inflight'])
        backlog = {key: restore_delivery(value, definition, registration_id)
                   for key, value in checkpoint['backlog'].items()}
        overflowed = checkpoint['overflowed']
        baselined.update(key for key, state in remote_state.items() if state.get('last_success') is not None)

    async def persist() -> None:
        if save is not None:
            await save(data({'version': 1,
                'detectors': {key: detector.checkpoint() for key, detector in detectors.items()},
                'remote_sources': remote_state,
                'schedulers': {key: source.export_checkpoint(wall_now=wall_clock(), monotonic_now=clock())
                               for key, source in schedulers.items()},
                'pending': {key: {'delivery': delivery_data(item.delivery), 'attempts': item.attempts,
                                  'paused': item.paused, 'terminal': item.terminal, 'inflight': item.inflight}
                            for key, item in pending.items()},
                'backlog': {key: delivery_data(value) for key, value in backlog.items()},
                'overflowed': overflowed}))

    def report(kind: str, *, source: str = "", delivery: Delivery | None = None, error: str = "",
               receipt: DeliveryResult | None = None) -> None:
        emit(
            RuntimeEvent(
                kind,
                registration_id,
                source_id=source,
                route_id=delivery.route_id if delivery else "",
                delivery_id=delivery.id if delivery else "",
                error=error,
                event_ids=tuple(event.id for event in delivery.events) if delivery else (),
                receipt_status=receipt.receipt_status if receipt else '',
                message_state=receipt.message_state if receipt else '',
                retry_on_resume=receipt.retry_on_resume if receipt else False,
            )
        )

    def enqueue(route_id: str, events: tuple[Event, ...]) -> bool:
        created = Delivery(uuid.uuid4().hex, registration_id, route_id, events)
        current = pending.get(route_id)
        # Never alter a delivery after its first attempt: a lost ACK may hide acceptance.
        previous = current.delivery if current is not None and current.attempts == 0 else backlog.get(route_id)
        candidate = replace(previous, events=previous.events + events) if previous else created
        try:
            if validate is not None:
                validate(candidate)
            valid = _bounded(candidate)
        except (ValueError, TypeError, UnicodeError):
            valid = False
        if not valid or previous is None and len(pending) + len(backlog) >= MAX_PENDING:
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

    async def collect(source: SourceDef | RemoteSourceDef) -> FileSnapshot | PollResult:
        if isinstance(source, SourceDef):
            return await scanner(source)
        return await remote.poll(source, dict(remote_state.get(source.id, {})), now=wall_clock())

    async def send(value: Delivery) -> DeliveryResult:
        return await deliver(value)

    report("baselining")
    for identifier in baselined:
        report("watching", source=identifier)
    for item in pending.values():
        if item.terminal:
            report('delivery_outcome_unknown', delivery=item.delivery,
                   error='Interrupted delivery is unknown; never automatically replayed')
    for identifier, state in remote_state.items():
        if state.get('failures', 0) or state.get('gap'):
            source_errors.add(identifier)
            report('source_error', source=identifier, error=state.get('error', 'Previous remote read failed'))
    for identifier in schedulers:
        report("watching", source=identifier)
    try:
        while not cancel.is_set():
            now = clock()
            # A deadline that raced the completion ACK was still busy. Capture
            # source ownership before removing settled pending deliveries, just
            # like the existing managed scheduler actor's poll-before-ACK rule.
            busy_sources = {route_sources[key] for key in pending} if save is not None else set()
            if resume is not None and resume.is_set():
                resume.clear()
                if overflowed or any(item.terminal for item in pending.values()):
                    report("paused", error="Execution/overflow cannot be replayed: stop and restart to rebaseline")
                else:
                    for item in pending.values():
                        item.paused = False
                        item.retry_at = now
            # Resolve processing before touching source evidence. An ambiguous
            # result freezes observation immediately, not after another scan.
            for route_id, item in list(pending.items()):
                if item.task is None or not item.task.done():
                    continue
                finished, item.task = item.task, None
                item.inflight = False
                try:
                    result = finished.result()
                    if not isinstance(result, DeliveryResult):
                        raise TypeError("Handler returned no DeliveryResult; execution outcome is unknown")
                except asyncio.CancelledError:
                    result = DeliveryResult("outcome-unknown", "Handler task cancelled after a possible send")
                except Exception as exc:
                    result = DeliveryResult("outcome-unknown", str(exc))
                if result.status in {'succeeded', 'queued'}:
                    report('delivery_queued' if result.status == 'queued' else 'delivery_succeeded',
                           delivery=item.delivery, receipt=result)
                    del pending[route_id]
                    if route_id in backlog:
                        pending[route_id] = _Pending(backlog.pop(route_id))
                elif result.status == "not-sent":
                    report("delivery_retry", delivery=item.delivery, error=result.error)
                    item.retry_at = now + min(5.0, 0.25 * 2 ** min(item.attempts - 1, 5))
                else:
                    item.paused = True
                    durable_retry = result.status == 'outcome-unknown' and result.retry_on_resume
                    item.terminal = result.status != "paused" and not durable_retry
                    if item.terminal or result.status == 'outcome-unknown':
                        report(
                            "delivery_" + result.status.replace("-", "_"), delivery=item.delivery,
                            error=result.error, receipt=result
                        )
                    report(
                        "paused",
                        delivery=item.delivery,
                        error=(
                            result.error + "; stop/rebaseline, never blind retry" if item.terminal else result.error
                        ),
                        receipt=result,
                    )
            blocked = overflowed or any(item.paused for item in pending.values())
            paused = blocked or suspended is not None and suspended.is_set()
            if cancel.is_set():
                break
            for source_id, scheduler in schedulers.items():
                was_completed = scheduler.completed
                timed_snapshot = scheduler.poll(wall_now=wall_clock(), monotonic_now=now,
                    suspended=bool(paused or source_id in busy_sources))
                if timed_snapshot.completed and not was_completed:
                    report("source_completed", source=source_id)
                if not paused:
                    for timed_detector in timed_detectors.values():
                        if timed_detector.definition.source == source_id:
                            timed_detector.observe(timed_snapshot)
            if paused:
                await persist()
                await sleep(0.05)
                continue
            watching: list[str] = []
            for source in sources.values():
                task = scans.get(source.id)
                if task is not None and task.done():
                    del scans[source.id]
                    next_scan[source.id] = now + (source.poll_interval_ms / 1000 if isinstance(source, SourceDef)
                                                  else source.poll_interval_seconds)
                    try:
                        snapshot = task.result()
                    except SourceError as exc:
                        source_errors.add(source.id)
                        report("source_error", source=source.id, error=str(exc))
                    else:
                        if isinstance(snapshot, PollResult):
                            remote_state[source.id] = snapshot.state
                            next_scan[source.id] = now + max(snapshot.delay, snapshot.state.get(
                                'next_poll_at', wall_clock()) - wall_clock())
                            for detector in detectors.values():
                                if isinstance(detector, RecordDetector) and detector.definition.source == source.id:
                                    detector.observe(snapshot.snapshot, now=now)
                            healthy = snapshot.snapshot.healthy
                            if not healthy:
                                source_errors.add(source.id)
                                report('source_error', source=source.id, error=snapshot.snapshot.error)
                        else:
                            healthy = True
                            for detector in detectors.values():
                                if isinstance(detector, FileChangeDetector) and detector.definition.source == source.id:
                                    detector.observe(snapshot, now=now)
                        if healthy and (source.id not in baselined or source.id in source_errors):
                            baselined.add(source.id)
                            source_errors.discard(source.id)
                            watching.append(source.id)
                if not overflowed and source.id not in scans and now >= next_scan[source.id]:
                    scans[source.id] = asyncio.create_task(collect(source))
            if not overflowed:
                for active_detector in active_detectors:
                    events = active_detector.flush(now=now)
                    if not events:
                        continue
                    for event in events:
                        emit(
                            RuntimeEvent(
                                "event_detected", registration_id, source_id=event.source_id, event_ids=(event.id,)
                            )
                        )
                    for routed in router.route(events):
                        if not enqueue(routed.route_id, routed.events):
                            overflowed = True
                            break
                    if overflowed:
                        break
            ready = [item for item in pending.values()
                     if not overflowed and not item.paused and item.task is None and now >= item.retry_at]
            for item in ready:
                item.attempts += 1
                item.inflight = True
            # Cursor, debounced detector state and immutable pending identities
            # commit together BEFORE any handler can observe the delivery.
            await persist()
            # Readiness acknowledges a recoverable cursor, not just an in-memory
            # scan. A caller may crash the daemon as soon as start returns.
            for source_id in watching:
                report("watching", source=source_id)
            for item in ready:
                report("delivery_processing", delivery=item.delivery)
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
        closing = asyncio.create_task(remote.close())
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                continue
        closing.result()
        report("stopped")


class Registration:
    """Own one live task; durable checkpoint data stays outside this handle."""

    def __init__(
        self,
        identifier: str,
        definition: ObserverDef,
        task: asyncio.Task[None],
        cancel: asyncio.Event,
        resume: asyncio.Event,
    ) -> None:
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
