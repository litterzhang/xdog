"""Durable owner-scoped composition of the existing observation executor."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xdog.eyes.checkpoint import delivery_data
from xdog.eyes.daemon_store import DaemonStore
from xdog.eyes.deliveries import Delivery
from xdog.eyes.events import DeliveryResult, RuntimeEvent
from xdog.eyes.executor import observe
from xdog.eyes.handlers.contracts import EventHandler, HandlerHealthError
from xdog.eyes.handlers.registry import builtin_handlers
from xdog.eyes.loader import definition_data, parse
from xdog.eyes.local_control import JsonValue, RuntimeLocation, object_value, text
from xdog.eyes.models import ObserverDef
from xdog.eyes.sources.file import scan_async


def _normalized_definition(value: object) -> ObserverDef:
    """Accept snapshots only: no daemon cwd or management path resolves sources."""
    definition = parse(value, base_dir=Path('/'))
    if definition_data(definition) != value:
        raise ValueError('Eyes daemon requires a normalized definition with absolute source roots and paths')
    return definition


@dataclass
class Active:
    task: asyncio.Task[None] | None = None
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    resume: asyncio.Event = field(default_factory=asyncio.Event)
    sources: dict[str, str] = field(default_factory=dict)
    routes: dict[str, str] = field(default_factory=dict)
    source_errors: dict[str, str] = field(default_factory=dict)
    handlers: dict[str, EventHandler] = field(default_factory=dict)
    error: str = ''
    ready: bool = False


class DaemonObservers:
    def __init__(self, location: RuntimeLocation, *,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.clock, self.wall_clock, self.sleep = clock, wall_clock, sleep
        self.store = DaemonStore(location.directory)
        self.active: dict[str, Active] = {}
        self.lock = asyncio.Lock()
        self.closing = False

    async def open(self) -> None:
        await self.store.open()

    async def recover(self) -> None:
        # Admission and recovery share the same fence. Re-read under the lock:
        # a pause/cancel received after listen must never activate a stale row.
        async with self.lock:
            for row in await self.store.rows():
                if not self.closing and row['state'] == 'running' and row['recover']:
                    await self.activate(row)

    async def set_state(self, identifier: str, state: str, error: str = '') -> None:
        await self.store.run(lambda db: db.execute(
            'UPDATE registrations SET state=?, error=? WHERE id=?', (state, error[:2048], identifier)).rowcount)

    async def activate(self, row: dict[str, Any]) -> None:
        identifier = row['id']
        existing = self.active.get(identifier)
        if existing is not None and existing.task is not None and not existing.task.done():
            return
        active = Active()
        self.active[identifier] = active

        async def start() -> None:
            try:
                await self._activate(row, active)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                active.error = str(exc)[:2048]
                await self.set_state(identifier, 'needs-review', active.error)
            finally:
                await asyncio.gather(*(handler.close() for handler in active.handlers.values()),
                                     return_exceptions=True)

        # This task owns connection recovery AND observation. halt joins this exact
        # task before releasing the admission fence; nothing can publish late.
        active.task = asyncio.create_task(start(), name='eyes-daemon:' + identifier)

    async def _activate(self, row: dict[str, Any], active: Active) -> None:
        identifier = row['id']
        definition = _normalized_definition(json.loads(row['content']))
        active.sources = {source.id: 'baselining' for source in definition.sources}
        checkpoint = json.loads(row['checkpoint']) if row['checkpoint'] else None
        # Never turn uncertain external effects into a fresh baseline or a send.
        if row['mode'] != 'events' and checkpoint is not None and any(
                item['inflight'] or item['terminal'] for item in checkpoint['pending'].values()):
            await self.set_state(identifier, 'needs-review', 'Interrupted/unknown delivery; not replayed')
            return
        if row['mode'] == 'handlers':
            registry = builtin_handlers()
            active.handlers = {handler.id: registry.create(handler)
                               for handler in definition.handlers}
            connections = [asyncio.create_task(handler.start()) for handler in active.handlers.values()]
            try:
                await asyncio.gather(*connections)
                for name, handler in active.handlers.items():
                    binding = handler.snapshot().details
                    binding.pop('connected', None)
                    encoded = json.dumps(binding, sort_keys=True, allow_nan=False)
                    def bind(db: Any, name: str = name, encoded: str = encoded) -> None:
                        previous = db.execute('SELECT binding FROM handler_bindings '
                            'WHERE registration_id=? AND handler_id=?', (identifier, name)).fetchone()
                        if previous is not None and previous[0] != encoded:
                            raise ValueError('Original ACP owner/inbox/branch binding changed; no work was resent')
                        db.execute('INSERT OR IGNORE INTO handler_bindings VALUES (?,?,?)',
                                   (identifier, name, encoded))
                    await self.store.run(bind)
            except BaseException:
                for connection in connections:
                    connection.cancel()
                await asyncio.gather(*connections, return_exceptions=True)
                raise
        routes = {route.id: route for route in definition.routes}

        def emit(event: RuntimeEvent) -> None:
            if event.kind in {'watching', 'source_completed', 'source_error'}:
                active.sources[event.source_id] = {'watching': 'watching', 'source_completed': 'completed',
                                                    'source_error': 'error'}[event.kind]
            if event.route_id:
                active.routes[event.route_id] = event.kind
            if event.kind == 'source_error':
                active.source_errors[event.source_id] = event.error
            elif event.kind == 'watching':
                active.source_errors.pop(event.source_id, None)
            elif event.error:
                active.error = event.error

        async def deliver(value: Delivery) -> DeliveryResult:
            if row['mode'] == 'events':
                encoded = json.dumps(delivery_data(value), allow_nan=False)
                def commit(db: Any) -> None:
                    old = db.execute('SELECT delivery FROM events WHERE delivery_id=?', (value.id,)).fetchone()
                    if old is not None and old[0] != encoded:
                        raise ValueError('Immutable Eyes event identity changed')
                    db.execute('INSERT OR IGNORE INTO events(registration_id,delivery_id,delivery) VALUES (?,?,?)',
                               (identifier, value.id, encoded))
                await self.store.run(commit)
                # Durable consumer ACK is backpressure, not an Agent permission.
                # Restart reuses the same delivery row and never auto-acknowledges.
                while True:
                    acknowledged = await self.store.run(lambda db: db.execute(
                        'SELECT 1 FROM acknowledgements a JOIN events e ON e.sequence<=a.sequence '
                        'WHERE a.owner=? AND e.delivery_id=?',
                        (row['owner'], value.id)).fetchone() is not None)
                    if acknowledged:
                        return DeliveryResult('succeeded')
                    await self.sleep(.1)
            return await active.handlers[routes[value.route_id].handler].deliver(value)

        def validate(value: Delivery) -> None:
            if row['mode'] == 'handlers':
                active.handlers[routes[value.route_id].handler].validate(value)

        active.ready = True
        await observe(definition, registration_id=identifier, scanner=scan_async, deliver=deliver,
            emit=emit, clock=self.clock, sleep=self.sleep, cancel=active.cancel,
            resume=active.resume, validate=validate,
            checkpoint=checkpoint, save=lambda value: self.store.checkpoint(identifier, value),
            replay_safe=row['mode'] == 'events', wall_clock=self.wall_clock)

    async def halt(self, identifier: str) -> None:
        active = self.active.pop(identifier, None)
        if active is not None and active.task is not None:
            active.cancel.set()
            active.task.cancel()
            await asyncio.gather(active.task, return_exceptions=True)

    async def dispatch(self, action: str, body: dict[str, JsonValue]) -> tuple[str, dict[str, JsonValue]]:
        if action == 'daemon-status':
            object_value(body, allowed=set())
            return 'ok', {'state': 'running', 'registrations': len(await self.store.rows())}
        allowed = {'owner', 'registration_id', 'definition', 'key', 'delivery', 'recover',
                   'after', 'limit', 'sequence', 'source_checkpoints'}
        object_value(body, allowed=allowed, required={'owner'})
        owner = text(body['owner'])
        async with self.lock:
            if self.closing:
                raise RuntimeError('Eyes daemon is closing')
            rows = await self.store.rows(owner)
            if action == 'register':
                definition = _normalized_definition(body.get('definition'))
                key, mode = text(body.get('key', definition.name)), text(body.get('delivery', 'handlers'))
                recover = body.get('recover', True)
                if mode not in {'events', 'handlers'} or type(recover) is not bool:
                    raise ValueError('Expected events/handlers delivery and boolean recover')
                content = json.dumps(definition_data(definition), sort_keys=True, allow_nan=False)
                previous = next((row for row in rows if row['key'] == key), None)
                if previous is not None:
                    if (previous['content'], previous['mode'], bool(previous['recover'])) != (content, mode, recover):
                        raise ValueError('Eyes registration key already has different content')
                    if previous['state'] == 'running':
                        await self.activate(previous)
                    previous = next(item for item in await self.store.rows(owner)
                                    if item['id'] == previous['id'])
                    return 'duplicate', self.snapshot(previous)
                identifier = 'eyes-' + uuid.uuid4().hex
                # Admission validates deadlines BEFORE a durable accepted registration.
                from xdog.eyes.sources.scheduler import prepare_schedulers
                if 'source_checkpoints' in body:
                    from xdog.eyes.models import SchedulerSourceDef
                    from xdog.eyes.sources.scheduler import SchedulerSource
                    cursors = object_value(body['source_checkpoints'])
                    expected = {item.id for item in definition.sources if isinstance(item, SchedulerSourceDef)}
                    if set(cursors) != expected:
                        raise ValueError('Source checkpoints must match the original scheduler definition')
                    schedulers = {item.id: SchedulerSource.restore(item, cursors[item.id],
                                  wall_now=self.wall_clock(), monotonic_now=self.clock())
                                  for item in definition.sources if isinstance(item, SchedulerSourceDef)}
                else:
                    schedulers = prepare_schedulers(definition, wall_now=self.wall_clock(), monotonic_now=self.clock())
                checkpoint = None
                if schedulers:
                    checkpoint = json.dumps({'version': 1, 'detectors': {}, 'pending': {}, 'backlog': {},
                        'overflowed': False, 'schedulers': {key: source.export_checkpoint(
                            wall_now=self.wall_clock(), monotonic_now=self.clock())
                            for key, source in schedulers.items()}})
                    # Mixed definitions need all detector slots, too.
                    from xdog.eyes.detectors.file_change import FileChangeDetector
                    from xdog.eyes.models import DetectorDef
                    value = json.loads(checkpoint)
                    value['detectors'] = {item.id: FileChangeDetector(item).checkpoint()
                                          for item in definition.detectors if isinstance(item, DetectorDef)}
                    checkpoint = json.dumps(value)
                await self.store.run(lambda db: db.execute(
                    'INSERT INTO registrations(id,owner,key,content,mode,recover,state,checkpoint) '
                    'VALUES (?,?,?,?,?,?,?,?)',
                    (identifier, owner, key, content, mode, recover, 'running', checkpoint)).rowcount)
                row = next(row for row in await self.store.rows(owner) if row['id'] == identifier)
                try:
                    await self.activate(row)
                except Exception as exc:
                    await self.set_state(identifier, 'needs-review', str(exc))
                    raise
                return 'accepted', self.snapshot(row)
            if action == 'events':
                return 'ok', await self.events(owner, body)
            if action == 'ack':
                return 'ok', await self.ack(owner, body.get('sequence'))
            selected = body.get('registration_id')
            if selected is None and action == 'owner-status':
                return 'ok', {'registrations': [self.snapshot(row) for row in rows]}
            selected_row = next((item for item in rows if item['id'] == selected or item['key'] == selected), None)
            if selected_row is None:
                raise ValueError('Unknown Eyes registration in this owner')
            row = selected_row
            if action == 'owner-status':
                return 'ok', self.snapshot(row)
            if action in {'pause', 'cancel'}:
                await self.halt(row['id'])
                row = next(item for item in await self.store.rows(owner) if item['id'] == row['id'])
                state = 'cancelled' if action == 'cancel' else 'paused'
                if row['state'] != 'cancelled' and (row['state'] != 'needs-review' or action == 'cancel'):
                    await self.set_state(row['id'], state)
                    row['state'] = state
                return 'accepted', self.snapshot(row)
            if action == 'owner-resume':
                checkpoint = json.loads(row['checkpoint']) if row['checkpoint'] else None
                if checkpoint and (checkpoint['overflowed'] or any(
                        item['terminal'] for item in checkpoint['pending'].values())):
                    raise ValueError('Unknown/terminal delivery cannot be resumed or replayed')
                if row['state'] in {'cancelled', 'needs-review'}:
                    raise ValueError('Cancelled/unknown work cannot be resumed or replayed')
                if row['state'] == 'paused' and checkpoint is not None:
                    from xdog.eyes.models import SchedulerSourceDef
                    from xdog.eyes.sources.scheduler import SchedulerSource
                    definition = _normalized_definition(json.loads(row['content']))
                    for source in definition.sources:
                        if isinstance(source, SchedulerSourceDef):
                            wall, mono = self.wall_clock(), self.clock()
                            restored = SchedulerSource.restore(source, checkpoint['schedulers'][source.id],
                                                               wall_now=wall, monotonic_now=mono)
                            restored.poll(wall_now=wall, monotonic_now=mono, suspended=True)
                            checkpoint['schedulers'][source.id] = restored.export_checkpoint(
                                wall_now=wall, monotonic_now=mono)
                    await self.store.checkpoint(row['id'], checkpoint)
                    row['checkpoint'] = json.dumps(checkpoint)
                active = self.active.get(row['id'])
                if (active is not None and active.task is not None and not active.task.done()
                        and checkpoint and any(item['paused'] for item in checkpoint['pending'].values())):
                    recovered = await asyncio.gather(*(handler.resume() for handler in active.handlers.values()))
                    if not recovered or not all(recovered):
                        raise ValueError('Original handler cannot safely resume this delivery')
                    active.error = ''
                    active.resume.set()
                await self.set_state(row['id'], 'running')
                row['state'] = 'running'
                await self.activate(row)
                # Activation is asynchronous; status/wait-ready reports quarantine.
                return 'accepted', self.snapshot(row)
            raise ValueError('Unknown owner-scoped Eyes action')

    def snapshot(self, row: dict[str, Any]) -> dict[str, Any]:
        active = self.active.get(row['id'])
        state = row['state']
        if state == 'running':
            if active is None:
                state = 'needs-start'
            elif active.error:
                state = 'paused'
            elif active.source_errors:
                state = 'degraded'
            elif active.ready and all(value in {'watching', 'completed'} for value in active.sources.values()):
                state = 'watching'
            else:
                state = 'baselining'
        checkpoint = json.loads(row['checkpoint']) if row['checkpoint'] else None
        deadlines = ([value['next_utc'] for value in checkpoint['schedulers'].values()
                      if not value['completed']] if checkpoint else [])
        return {'registration_id': row['id'], 'owner': row['owner'],
                'key': row['key'], 'state': state,
                'next_due': min(deadlines) if deadlines else None,
                'duplicate': False, 'sources': active.sources if active else {},
                'routes': active.routes if active else {}, 'error': (active.error if active else '') or row['error'],
                'source_health': {key: {field: value.get(field) for field in
                    ('last_success', 'last_status', 'failures', 'next_poll_at', 'gap', 'error')}
                    for key, value in (checkpoint or {}).get('remote_sources', {}).items()},
                'source_errors': dict(active.source_errors) if active else {}}

    async def events(self, owner: str, body: dict[str, JsonValue]) -> dict[str, JsonValue]:
        after, limit = body.get('after'), body.get('limit', 64)
        if after is not None and (type(after) is not int or after < 0):
            raise ValueError('Expected a nonnegative event cursor')
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError('Expected event limit 1..128')
        def read(db: Any) -> dict[str, JsonValue]:
            ack = db.execute('SELECT sequence FROM acknowledgements WHERE owner=?',
                             (owner,)).fetchone()
            cursor = (ack[0] if ack else 0) if after is None else after
            events: list[JsonValue] = []
            size = 0
            for row in db.execute('SELECT e.*, r.key FROM events e JOIN registrations r '
                'ON r.id=e.registration_id WHERE r.owner=? AND e.sequence>? '
                'ORDER BY e.sequence LIMIT ?', (owner, cursor, limit)):
                event = {'sequence': row['sequence'], 'key': row['key'], 'registration_id': row['registration_id'],
                         'delivery': json.loads(row['delivery'])}
                size += len(json.dumps(event, ensure_ascii=True).encode())
                if events and size > 900_000:
                    break
                events.append(event)
            return {'events': events}
        return await self.store.run(read)

    async def ack(self, owner: str, sequence: object) -> dict[str, JsonValue]:
        if type(sequence) is not int or sequence < 0:
            raise ValueError('Expected a nonnegative sequence')
        def commit(db: Any) -> None:
            if sequence and db.execute('SELECT 1 FROM events e JOIN registrations r ON r.id=e.registration_id '
                'WHERE e.sequence=? AND r.owner=?', (sequence, owner)).fetchone() is None:
                raise ValueError('Event cursor does not belong to this owner')
            db.execute('INSERT INTO acknowledgements VALUES (?,?) ON CONFLICT(owner) DO UPDATE '
                       'SET sequence=MAX(sequence,excluded.sequence)', (owner, sequence))
        await self.store.run(commit)
        return {'acknowledged': sequence}

    async def health(self) -> None:
        for identifier, active in tuple(self.active.items()):
            if not active.ready:
                continue
            for handler in active.handlers.values():
                try:
                    async with asyncio.timeout(2):
                        await handler.check()
                except Exception as exc:
                    # Receiver health IO must not hold the admission lock. Only
                    # the original active owner may publish its failure later.
                    async with self.lock:
                        if self.active.get(identifier) is not active:
                            break
                        active.error = str(exc)[:2048]
                        if active.task is not None:
                            active.task.cancel()
                            await asyncio.gather(active.task, return_exceptions=True)
                            await self.set_state(identifier,
                                'paused' if isinstance(exc, HandlerHealthError) and exc.recoverable else 'needs-review',
                                active.error)
                    break

    async def aclose(self) -> None:
        self.closing = True
        async with self.lock:
            for identifier in tuple(self.active):
                await self.halt(identifier)
