"""Durable single-schedule actor for an embedding host, without a harness dependency.

Hosts atomically replace the *entire* state passed to ``save``. A normal return
from ``deliver`` acknowledges actual host completion, not merely transport intake.
Exceptions stop the actor with its persisted pending identity intact. Restarting
is an explicit host decision; this module does not retry ambiguous execution.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass

from xdog.eyes.deliveries import Delivery
from xdog.eyes.detectors.scheduler_due import SchedulerDueDetector
from xdog.eyes.loader import definition_data
from xdog.eyes.local_control import JsonValue, object_value, text
from xdog.eyes.models import AtSchedule, Event, ObserverDef, SchedulerDetectorDef, SchedulerEvidence, SchedulerSourceDef
from xdog.eyes.router import EventRouter
from xdog.eyes.sources.scheduler import SchedulerSource


@dataclass(frozen=True)
class SchedulerClocks:
    wall: Callable[[], float] = time.time
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


def _number(value: JsonValue) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('Expected finite scheduler timestamp')
    return value  # Preserve JSON integer/float representation for immutable enqueue identity.


def _delivery_data(delivery: Delivery) -> dict[str, JsonValue]:
    # Round-trip to lists, not tuples; only immutable domain data, never clients.
    return object_value(json.loads(json.dumps(asdict(delivery), allow_nan=False)))


def _restore_delivery(value: object, definition: ObserverDef, registration_id: str) -> Delivery:
    keys = {'id', 'registration_id', 'route_id', 'events'}
    data = object_value(value, allowed=keys, required=keys)
    route = definition.routes[0]
    detector = definition.detectors[0]
    if data['registration_id'] != registration_id or data['route_id'] != route.id:
        raise ValueError('Pending delivery registration/route mismatch')
    values = data['events']
    if not isinstance(values, list) or len(values) != 1:
        raise ValueError('Expected exactly one pending scheduler event')
    event_keys = {'id', 'source_id', 'detector_id', 'type', 'observed_at', 'evidence'}
    event = object_value(values[0], allowed=event_keys, required=event_keys)
    if (event['source_id'] != detector.source or event['detector_id'] != detector.id
            or event['type'] != 'scheduler.due'):
        raise ValueError('Pending event does not match scheduler definition')
    evidence = event['evidence']
    if not isinstance(evidence, list) or len(evidence) != 1:
        raise ValueError('Expected exactly one scheduler evidence')
    evidence_keys = {'schedule_id', 'scheduled_at', 'observed_at', 'occurrence_id', 'timezone'}
    item = object_value(evidence[0], allowed=evidence_keys, required=evidence_keys)
    if item['schedule_id'] != detector.source or item['observed_at'] != event['observed_at']:
        raise ValueError('Pending evidence provenance mismatch')
    occurrence = SchedulerEvidence(text(item['schedule_id']), _number(item['scheduled_at']),
                                   _number(item['observed_at']), text(item['occurrence_id']), text(item['timezone']))
    restored = Event(text(event['id']), detector.source, detector.id, 'scheduler.due',
                     _number(event['observed_at']), (occurrence,))
    return Delivery(text(data['id']), registration_id, route.id, (restored,))


def _validate(definition: ObserverDef) -> tuple[SchedulerSourceDef, SchedulerDetectorDef]:
    if (len(definition.sources) != 1 or len(definition.detectors) != 1 or len(definition.routes) != 1
            or len(definition.handlers) != 1):
        raise ValueError('Managed scheduler requires one source, detector, handler and route')
    source, detector, route = definition.sources[0], definition.detectors[0], definition.routes[0]
    if (not isinstance(source, SchedulerSourceDef) or not isinstance(detector, SchedulerDetectorDef)
            or detector.source != source.id or route.detector != detector.id
            or route.handler != definition.handlers[0].id or route.event_types != ('scheduler.due',)):
        raise ValueError('Managed scheduler requires a connected scheduler.due route')
    return source, detector


def _binding(definition: ObserverDef) -> str:
    return hashlib.sha256(json.dumps(definition_data(definition), sort_keys=True,
                                    allow_nan=False).encode()).hexdigest()


def initial_managed_state(definition: ObserverDef, registration_id: str,
                          clocks: SchedulerClocks = SchedulerClocks()) -> dict[str, JsonValue]:
    """Admit a fresh source before the host atomically persists its create ACK.

    The returned data is the actor's ordinary restore state. In particular an
    accepted one-off can be recovered after its deadline without fresh admission.
    No receiver is started and no code/client/credential is stored in the state.
    """
    _, state = _initial_source(definition, registration_id, clocks)
    return state


def _initial_source(definition: ObserverDef, registration_id: str,
                    clocks: SchedulerClocks) -> tuple[SchedulerSource, dict[str, JsonValue]]:
    source_def, _ = _validate(definition)
    text(registration_id)
    wall, mono = clocks.wall(), clocks.monotonic()
    source = SchedulerSource(source_def, wall_now=wall, monotonic_now=mono)
    state = object_value({'version': 1, 'binding': _binding(definition), 'registration_id': registration_id,
                          'cursor': source.export_checkpoint(wall_now=wall, monotonic_now=mono), 'pending': None})
    return source, state


def _state_data(definition: ObserverDef, registration_id: str, state: object) -> dict[str, JsonValue]:
    _validate(definition)
    text(registration_id)
    keys = {'version', 'binding', 'registration_id', 'cursor', 'pending'}
    data = object_value(state, allowed=keys, required=keys)
    if (type(data['version']) is not int or data['version'] != 1
            or data['binding'] != _binding(definition) or data['registration_id'] != registration_id):
        raise ValueError('Managed scheduler state version/definition/registration mismatch')
    return data


def restore_pending_delivery(definition: ObserverDef, registration_id: str, state: object) -> Delivery | None:
    """Decode the exact immutable pending identity for a host's atomic outbox save.

    Validate the whole state, including its definition-bound source checkpoint,
    without admitting a fresh source or depending on the current wall clock.
    """
    data = _state_data(definition, registration_id, state)
    source_def, _ = _validate(definition)
    source = SchedulerSource.restore(source_def, data['cursor'], wall_now=0., monotonic_now=0.)
    if data['pending'] is None:
        return None
    pending = _restore_delivery(data['pending'], definition, registration_id)
    if isinstance(source_def.schedule, AtSchedule) and not source.completed:
        raise ValueError('Prepared one-off must have a completed source cursor')
    return pending


async def _settle(*tasks: asyncio.Task[object]) -> bool:
    """Cancel once and join through repeated caller cancellations, without agents.

    Report newly received cancellation instead of raising it, so a caller's
    already active exception retains priority over cancellation during cleanup.
    Shield a separate drain task: cancelling a gather would re-cancel callbacks
    that may already be awaiting their own asynchronous cleanup.
    """
    for task in tasks:
        if not task.done() and not task.cancelling():
            task.cancel()

    async def drain() -> None:
        await asyncio.gather(*tasks, return_exceptions=True)

    joining = asyncio.create_task(drain())
    cancelled = False
    while not joining.done():
        try:
            await asyncio.shield(joining)
        except asyncio.CancelledError:
            cancelled = True
    joining.result()
    return cancelled


async def run_managed_scheduler(
    definition: ObserverDef,
    registration_id: str,
    state: object | None,
    save: Callable[[dict[str, JsonValue]], Awaitable[None]],
    deliver: Callable[[Delivery], Awaitable[object]],
    cancel: asyncio.Event,
    clocks: SchedulerClocks = SchedulerClocks(),
) -> None:
    """Prepare durably before send; commit cursor+pending together, fail closed.

    ``state=None`` admits a fresh schedule (past ``at`` is rejected). Non-null
    state is a trusted host checkpoint bound to this exact definition and ID.
    Recovery resends only the original pending Delivery, never a replacement ID.
    While delivery awaits execution, repeating deadlines continue to be skipped.
    One-offs already prepared remain pending even through disconnection/blocking.
    Event/task cancellation closes all owned tasks, including an in-flight callback.
    """
    source_def, detector_def = _validate(definition)
    if state is None:
        source, data = _initial_source(definition, registration_id, clocks)
        pending = None
    else:
        data = _state_data(definition, registration_id, state)
        source = SchedulerSource.restore(source_def, data['cursor'], wall_now=clocks.wall(),
                                         monotonic_now=clocks.monotonic())
        pending = restore_pending_delivery(definition, registration_id, data)
    binding = data['binding']
    detector, router = SchedulerDueDetector(detector_def), EventRouter(definition)

    async def run() -> None:
        nonlocal pending
        delivery_task: asyncio.Task[object] | None = None
        sleeper: asyncio.Task[None] | None = None
        cursor = data['cursor']

        async def persist() -> None:
            current: dict[str, JsonValue] = {
                'version': 1, 'binding': binding, 'registration_id': registration_id,
                'cursor': cursor, 'pending': _delivery_data(pending) if pending is not None else None,
            }
            # Callback ownership cannot mutate the actor's frozen cursor/pending.
            await save(object_value(json.loads(json.dumps(current, allow_nan=False))))

        async def send(value: Delivery) -> object:
            return await deliver(value)

        async def tick() -> None:
            await clocks.sleep(.2)

        try:
            # Fresh acceptance itself is durable, including an at not yet due.
            await persist()
            while not cancel.is_set():
                wall, mono = clocks.wall(), clocks.monotonic()
                before = source.export_checkpoint(wall_now=wall, monotonic_now=mono)
                previous_pending = pending
                detector.observe(source.poll(wall_now=wall, monotonic_now=mono, suspended=pending is not None))
                after = source.export_checkpoint(wall_now=wall, monotonic_now=mono)
                cursor_changed = before != after
                events = detector.flush(now=mono)
                if events:
                    routed, = router.route(events)
                    pending = Delivery(uuid.uuid4().hex, registration_id, routed.route_id, routed.events)
                # Poll suspended BEFORE commit even when ACK and a deadline race.
                if delivery_task is not None and delivery_task.done():
                    delivery_task.result()
                    delivery_task = None
                    pending = None
                if cursor_changed or pending != previous_pending:
                    cursor = after
                    await persist()
                if cancel.is_set():
                    return
                if pending is not None and delivery_task is None:
                    delivery_task = asyncio.create_task(send(pending))
                if source.completed and pending is None:
                    return
                sleeper = asyncio.create_task(tick())
                waiting: set[asyncio.Task[object] | asyncio.Task[None]] = {sleeper}
                if delivery_task is not None:
                    waiting.add(delivery_task)
                await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                if await _settle(sleeper):
                    raise asyncio.CancelledError
                sleeper = None
        finally:
            owned = [task for task in (delivery_task, sleeper) if task is not None]
            original = sys.exception()
            if await _settle(*owned) and original is None:
                raise asyncio.CancelledError

    if cancel.is_set():
        return
    work, cancelled = asyncio.create_task(run()), asyncio.create_task(cancel.wait())
    try:
        done, _ = await asyncio.wait({work, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        if work in done:
            work.result()
    finally:
        original = sys.exception()
        if await _settle(work, cancelled) and original is None:
            raise asyncio.CancelledError
