"""Embeddable observation lifecycle, without IPC, process leases or idle shutdown."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xdog.eyes.deliveries import Delivery
from xdog.eyes.events import DeliveryResult, RuntimeEvent
from xdog.eyes.executor import Registration, observe
from xdog.eyes.handlers.contracts import EventHandler, HandlerHealthError
from xdog.eyes.handlers.registry import HandlerRegistry, builtin_handlers
from xdog.eyes.loader import definition_data, parse
from xdog.eyes.local_control import JsonValue, object_value
from xdog.eyes.models import AtSchedule, HandlerDef, ObserverDef, SourceDef
from xdog.eyes.sources.file import FileSnapshot, scan_async
from xdog.eyes.sources.scheduler import SchedulerSource, prepare_schedulers


@dataclass
class Entry:
    registration: Registration
    handlers: dict[str, EventHandler]
    digest: str
    sources: dict[str, str]
    routes: dict[str, dict[str, Any]]
    suspended: asyncio.Event
    health: dict[str, str] = field(default_factory=dict)
    retired: str = ''
    resume_results: dict[str, bool] = field(default_factory=dict)


class ObserverService:
    def __init__(self, *, cwd: Path, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 scanner: Callable[[SourceDef], Awaitable[FileSnapshot]] = scan_async,
                 handler_factory: Callable[[HandlerDef], EventHandler] | None = None,
                 handler_registry: HandlerRegistry | None = None,
                 wall_clock: Callable[[], float] = time.time,
                 auto_health: bool = True) -> None:
        self.cwd = cwd.resolve()
        if not self.cwd.is_dir():
            raise ValueError('Eyes cwd must be an existing directory')
        self.clock, self.sleep, self.scanner = clock, sleep, scanner
        self.wall_clock = wall_clock
        self.handler_factory = handler_factory
        self.handler_registry = handler_registry if handler_registry is not None else builtin_handlers()
        self.entries: dict[str, Entry] = {}
        self.names: dict[str, str] = {}
        self._shutdown = asyncio.Event()
        self.lock = asyncio.Lock()
        self._starts: set[asyncio.Task[Any]] = set()
        self._pending_starts = 0
        self._closing = False
        self._auto_health = auto_health
        self._monitor_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None

    def _handler(self, definition: HandlerDef) -> EventHandler:
        if self.handler_factory is not None:
            return self.handler_factory(definition)
        return self.handler_registry.create(definition)

    async def start(self, data: object) -> dict[str, JsonValue]:
        task = asyncio.current_task()
        assert task is not None
        self._starts.add(task)
        try:
            result = await self._start(data)
            if self._auto_health and self._monitor_task is None:
                self._monitor_task = asyncio.create_task(self.monitor())
            return result
        finally:
            self._starts.discard(task)

    async def _start(self, data: object) -> dict[str, JsonValue]:
        definition = parse(definition_data(data) if isinstance(data, ObserverDef) else data,
                           base_dir=self.cwd, handler_registry=self.handler_registry)
        normalized = definition_data(definition)
        digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        async with self.lock:
            if self._closing or self._shutdown.is_set():
                raise RuntimeError('Eyes runtime is closing; no new registration admitted')
            existing = self.names.get(definition.name)
            if existing is not None:
                if self.entries[existing].digest != digest:
                    raise ValueError('Definition name is already active; stop it before changing the definition')
                return {'registration_id': existing, 'duplicate': True}
            schedulers = prepare_schedulers(definition, wall_now=self.wall_clock(), monotonic_now=self.clock())
            handlers = {item.id: self._handler(item) for item in definition.handlers}
            connections = [asyncio.create_task(handler.start()) for handler in handlers.values()]
            self._pending_starts += 1
            try:
                try:
                    await asyncio.gather(*connections)
                    # Repeating sources begin only after every receiver is ready.
                    # A timely admitted one-off retains its absolute deadline.
                    wall, mono = self.wall_clock(), self.clock()
                    schedulers = {key: source if isinstance(source.definition.schedule, AtSchedule)
                                  else SchedulerSource(source.definition, wall_now=wall, monotonic_now=mono)
                                  for key, source in schedulers.items()}
                except BaseException:
                    for task in connections:
                        task.cancel()
                    await asyncio.gather(*connections, return_exceptions=True)
                    await asyncio.gather(*(handler.close() for handler in handlers.values()), return_exceptions=True)
                    raise
                if self._closing:
                    await asyncio.gather(*(handler.close() for handler in handlers.values()), return_exceptions=True)
                    raise RuntimeError('Eyes runtime closed during target initialization')
                identifier = uuid.uuid4().hex
                cancel, resume, suspended = asyncio.Event(), asyncio.Event(), asyncio.Event()
                routes = {item.id: item for item in definition.routes}

                async def deliver(value: Delivery) -> DeliveryResult:
                    return await handlers[routes[value.route_id].handler].deliver(value)

                def validate(value: Delivery) -> None:
                    handlers[routes[value.route_id].handler].validate(value)

                task = asyncio.create_task(observe(definition, registration_id=identifier, scanner=self.scanner,
                    deliver=deliver, emit=lambda event: self.emit(identifier, event), clock=self.clock,
                    sleep=self.sleep, cancel=cancel, resume=resume,
                    suspended=suspended, validate=validate, wall_clock=self.wall_clock,
                    scheduler_sources=schedulers))
                registration = Registration(identifier, definition, task, cancel, resume)
                self.entries[identifier] = Entry(registration, handlers, digest,
                    {item.id: 'baselining' for item in definition.sources},
                    {item.id: {'state': 'idle', 'delivery_id': '', 'pending': [], 'error': '', 'paused': False,
                               'retry_on_resume': False} for item in definition.routes}, suspended)
                self.names[definition.name] = identifier
                return {'registration_id': identifier, 'duplicate': False}
            finally:
                self._pending_starts -= 1

    def emit(self, identifier: str, event: RuntimeEvent) -> None:
        entry = self.entries.get(identifier)
        if entry is None:
            return
        if event.kind == 'watching':
            entry.sources[event.source_id] = 'watching'
        elif event.kind == 'source_completed':
            entry.sources[event.source_id] = 'completed'
        elif event.kind == 'source_error':
            entry.sources[event.source_id] = 'error'
        route = entry.routes.get(event.route_id)
        if route is None:
            return
        if event.delivery_id and event.kind == 'delivery_pending' and event.delivery_id not in route['pending']:
            route['pending'].append(event.delivery_id)
            if not route['paused'] and route['state'] != 'processing':
                route.update(state='pending', delivery_id=event.delivery_id)
        elif event.kind == 'delivery_processing':
            route.update(state='processing', delivery_id=event.delivery_id, paused=False, error='')
        elif event.kind in {'delivery_succeeded', 'delivery_queued'}:
            if event.delivery_id in route['pending']:
                route['pending'].remove(event.delivery_id)
            route.update(state='queued' if event.kind == 'delivery_queued' else 'succeeded', paused=False,
                         error='', receipt_status=event.receipt_status, message_state=event.message_state,
                         delivery_id=event.delivery_id)
        elif event.kind.startswith('delivery_') and event.kind in {
            'delivery_failed', 'delivery_cancelled', 'delivery_outcome_unknown'
        }:
            route.update(state=event.kind.removeprefix('delivery_').replace('_', '-'), paused=True,
                         error=event.error[:2048], delivery_id=event.delivery_id,
                         retry_on_resume=event.retry_on_resume)
        elif event.kind == 'paused':
            route.update(paused=True, error=event.error[:2048], retry_on_resume=event.retry_on_resume)
            if route['state'] not in {'failed', 'cancelled', 'outcome-unknown'}:
                route['state'] = 'paused'
            if event.error.startswith('overflow:'):
                route['fatal'] = True

    def _state(self, entry: Entry) -> str:
        if entry.retired:
            return 'paused'
        if entry.health or any(route['paused'] for route in entry.routes.values()):
            return 'paused'
        task = entry.registration.task
        if task.done():
            return 'stopped' if task.cancelled() or task.exception() is None else 'failed'
        if any(value == 'error' for value in entry.sources.values()):
            return 'degraded'
        if any(value not in {'watching', 'completed'} for value in entry.sources.values()):
            return 'baselining'
        return 'watching'

    async def status(self, identifier: str | None = None) -> dict[str, JsonValue]:
        rows = []
        for key, entry in self.entries.items():
            if identifier is not None and identifier != key:
                continue
            rows.append({'registration_id': key, 'name': entry.registration.definition.name,
                         'state': self._state(entry), 'sources': entry.sources, 'routes': entry.routes,
                         'retired': entry.retired, 'health': entry.health, 'resume_results': entry.resume_results,
                         'handlers': {name: {'state': snapshot.state, **snapshot.details}
                                      for name, h in entry.handlers.items() for snapshot in (h.snapshot(),)}})
        return object_value(json.loads(json.dumps({'registrations': rows}, ensure_ascii=True, allow_nan=False)))

    async def stop_entry(self, identifier: str) -> dict[str, JsonValue]:
        async with self.lock:
            entry = self.entries.get(identifier)
            if entry is None:
                raise ValueError('Unknown active Eyes registration')
            pending: list[JsonValue] = [name for name, route in entry.routes.items()
                                        if route['pending'] or route['paused']]
            await entry.registration.aclose()
            await asyncio.gather(*(handler.close() for handler in entry.handlers.values()))
            self.entries.pop(identifier, None)
            self.names.pop(entry.registration.definition.name, None)
            return {'registration_id': identifier, 'stopped': True, 'shutdown': not self.entries,
                    'pending_or_unknown': pending,
                    'warning': ('Unsent/uncertain evidence may remain; inspect destination before rebaseline'
                                if pending else ''), 'notice': 'Durable destination messages are never deleted'}

    async def resume_entry(self, identifier: str) -> bool:
        entry = self.entries.get(identifier)
        if entry is None or entry.retired or entry.registration.task.done():
            return False
        if any(route.get('fatal') or route['state'] in {'failed', 'cancelled'}
               or route['state'] == 'outcome-unknown' and not route['retry_on_resume']
               for route in entry.routes.values()):
            return False
        entry.resume_results = {}
        for name, handler in entry.handlers.items():
            accepted = await handler.resume()
            entry.resume_results[name] = accepted
            if accepted:
                entry.health.pop(name, None)
        accepted = any(entry.resume_results.values())
        if accepted:
            if not entry.health:
                entry.suspended.clear()
            entry.registration.resume.set()
        return accepted

    async def health(self) -> dict[str, JsonValue]:
        for identifier, entry in tuple(self.entries.items()):
            if entry.retired:
                continue
            for name, handler in entry.handlers.items():
                try:
                    async with asyncio.timeout(2):
                        await handler.check()
                except Exception as exc:
                    entry.health[name] = str(exc)[:2048]
                    entry.suspended.set()
                    if not isinstance(exc, HandlerHealthError) or not exc.recoverable:
                        entry.retired = str(exc)[:2048]
                        await entry.registration.aclose()
                        await asyncio.gather(*(h.close() for h in entry.handlers.values()), return_exceptions=True)
                        break
        return await self.status()

    async def monitor(self) -> None:
        while not self._shutdown.is_set():
            await self.health()
            await self.sleep(0.2)

    async def stop(self, identifier: str) -> dict[str, JsonValue]:
        return await self.stop_entry(identifier)

    async def resume(self, identifier: str) -> bool:
        return await self.resume_entry(identifier)

    async def _close(self) -> None:
        self._closing = True
        self._shutdown.set()
        pending = set(self._starts)
        if self._monitor_task is not None:
            pending.add(self._monitor_task)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        failures = []
        for identifier in tuple(self.entries):
            try:
                await self.stop_entry(identifier)
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise failures[0]

    async def aclose(self) -> None:
        """Idempotent awaited shutdown; caller cancellation cannot leak owned tasks."""
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError
