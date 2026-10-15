"""Independent observation lifecycle; ACP is loaded only when explicitly activating a definition."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import signal
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from xdog.eyes.events import RuntimeEvent
from xdog.eyes.executor import Registration, observe
from xdog.eyes.loader import definition_data, parse
from xdog.eyes.local_control import (
    MAX_FRAME,
    ControlError,
    JsonValue,
    RuntimeLocation,
    decode_frame,
    encode_frame,
    object_value,
    private_open,
    publish_location,
    runtime_location,
    text,
    unlink_owned,
    validate_location,
    validate_socket,
)
from xdog.eyes.models import HandlerDef, ObserverDef, SourceDef
from xdog.eyes.sources.file import FileSnapshot, scan_async

if TYPE_CHECKING:
    from xdog.eyes.handlers.acp import AcpHandler


@dataclass
class Entry:
    registration: Registration
    handlers: dict[str, AcpHandler]
    digest: str
    sources: dict[str, str]
    routes: dict[str, dict[str, Any]]
    suspended: asyncio.Event
    health: dict[str, str] = field(default_factory=dict)
    retired: str = ''
    resume_results: dict[str, bool] = field(default_factory=dict)


class ObserverRuntime:
    def __init__(self, location: RuntimeLocation, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 scanner: Callable[[SourceDef], Awaitable[FileSnapshot]] = scan_async,
                 handler_factory: Callable[[HandlerDef], AcpHandler] | None = None) -> None:
        validate_location(location)
        self.location = replace(location, generation=uuid.uuid4().hex)
        self.clock, self.sleep, self.scanner = clock, sleep, scanner
        self.handler_factory = handler_factory
        self.entries: dict[str, Entry] = {}
        self.names: dict[str, str] = {}
        self.stop = asyncio.Event()
        self.lock = asyncio.Lock()
        self._controls: set[asyncio.Task[Any]] = set()
        self._starts: set[asyncio.Task[Any]] = set()
        self._pending_starts = 0
        self._since = self.clock()
        self._lease = -1
        self._socket_identity: tuple[int, int] | None = None
        self._record_identity: tuple[int, int] | None = None
        self._closing = False

    def _handler(self, definition: HandlerDef) -> AcpHandler:
        if self.handler_factory is not None:
            return self.handler_factory(definition)
        from xdog.eyes.handlers.acp import AcpHandler
        return AcpHandler(definition, cwd=self.location.cwd)

    async def start(self, data: object) -> dict[str, JsonValue]:
        task = asyncio.current_task()
        assert task is not None
        self._starts.add(task)
        try:
            return await self._start(data)
        finally:
            self._starts.discard(task)

    async def _start(self, data: object) -> dict[str, JsonValue]:
        definition = parse(definition_data(data) if isinstance(data, ObserverDef) else data,
                           base_dir=self.location.cwd)
        normalized = definition_data(definition)
        digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        async with self.lock:
            if self._closing or self.stop.is_set():
                raise RuntimeError('Eyes runtime is closing; no new registration admitted')
            existing = self.names.get(definition.name)
            if existing is not None:
                if self.entries[existing].digest != digest:
                    raise ValueError('Definition name is already active; stop it before changing the definition')
                return {'registration_id': existing, 'duplicate': True}
            handlers = {item.id: self._handler(item) for item in definition.handlers}
            connections = [asyncio.create_task(handler.connect()) for handler in handlers.values()]
            self._pending_starts += 1
            try:
                try:
                    await asyncio.gather(*connections)
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

                async def deliver(value: Any) -> Any:
                    return await handlers[routes[value.route_id].handler].deliver(value)

                task = asyncio.create_task(observe(definition, registration_id=identifier, scanner=self.scanner,
                    deliver=deliver, emit=lambda event: self.emit(identifier, event), clock=self.clock,
                    sleep=self.sleep, cancel=cancel, resume=resume, cwd=self.location.cwd, suspended=suspended))
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
        if any(value != 'watching' for value in entry.sources.values()):
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
                         'handlers': {name: {'session': h.definition.session, 'delivery': h.definition.delivery,
                             'state': h.state, 'connected': h.client.connected,
                             'owner_generation': h.owner_generation} for name, h in entry.handlers.items()}})
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
            if handler.definition.delivery == 'enqueue' and name in entry.health:
                accepted = await handler.reconnect()
            else:
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

    async def monitor(self) -> None:
        while not self.stop.is_set():
            if not self.entries and not self._pending_starts and self.clock() - self._since >= 10:
                self.stop.set()
                break
            for identifier, entry in tuple(self.entries.items()):
                if entry.retired:
                    continue
                for name, handler in entry.handlers.items():
                    if handler.state in {'processing', 'outcome-unknown', 'closed'}:
                        continue  # Settle a sent request before interpreting its connection loss.
                    try:
                        if not handler.client.connected:
                            raise ConnectionError('ACP connection ended; explicit resume/rebaseline required')
                        async with asyncio.timeout(2):
                            if handler.definition.delivery == 'enqueue':
                                await handler.client.inbox_status()
                            elif handler.owner_generation is not None:
                                if await handler.client.generation() != handler.owner_generation:
                                    raise ValueError('ACP owner generation changed')
                    except Exception as exc:
                        entry.health[name] = str(exc)[:2048]
                        entry.suspended.set()
                        if handler.definition.delivery == 'prompt':
                            entry.retired = str(exc)[:2048]
                            await entry.registration.aclose()
                            await asyncio.gather(*(h.close() for h in entry.handlers.values()), return_exceptions=True)
                            break
            await self.sleep(0.2)

    async def dispatch(self, value: object) -> tuple[str, dict[str, JsonValue]]:
        row = object_value(value, allowed={'version', 'generation', 'action', 'body'},
                           required={'version', 'generation', 'action', 'body'})
        if type(row['version']) is not int or row['version'] != 1:
            raise ControlError('Unsupported Eyes control version')
        action, generation = text(row['action']), text(row['generation'], empty=True)
        if generation != self.location.generation and not (action == 'hello' and generation == ''):
            return 'stale-generation', {'error': 'Eyes runtime changed; inspect status before retrying'}
        body = object_value(row['body'])
        if action == 'hello':
            object_value(body, allowed=set())
            return 'ok', {}
        if action == 'start':
            object_value(body, allowed={'definition'}, required={'definition'})
            result = await self.start(body['definition'])
            return 'duplicate' if result['duplicate'] else 'accepted', result
        if action == 'status':
            object_value(body, allowed={'registration_id'})
            return 'ok', await self.status(text(body['registration_id']) if 'registration_id' in body else None)
        if action in {'stop', 'resume'}:
            object_value(body, allowed={'registration_id'}, required={'registration_id'})
            identifier = text(body['registration_id'])
            if action == 'stop':
                return 'accepted', await self.stop_entry(identifier)
            accepted = await self.resume_entry(identifier)
            return ('accepted' if accepted else 'rejected'), {
                'registration_id': identifier, 'resumed': accepted,
                'notice': 'Enqueue resume only collects data; prompt resume requests target permission'}
        raise ControlError('Unknown Eyes lifecycle action; this endpoint does not receive agent events')

    async def serve(self) -> None:
        validate_location(self.location)
        self._lease = private_open(self.location.directory / 'owner.lock', os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._lease)
            self._lease = -1
            raise RuntimeError('An Eyes runtime already owns this project') from exc
        writers: set[asyncio.StreamWriter] = set()
        server = None
        monitor = None

        async def connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            work = gone = None
            shutdown = False
            try:
                async with asyncio.timeout(5):
                    frame = decode_frame(await reader.readline())
                work = asyncio.create_task(self.dispatch(frame))
                self._controls.add(work)
                gone = asyncio.create_task(reader.read(1))
                async with asyncio.timeout(125):
                    done, _ = await asyncio.wait({work, gone}, return_when=asyncio.FIRST_COMPLETED)
                    if work not in done:
                        work.cancel()
                        await asyncio.gather(work, return_exceptions=True)
                        return
                    try:
                        status, body = work.result()
                        shutdown = frame.get('action') == 'stop' and body.get('shutdown') is True
                    except (ValueError, RuntimeError, OSError, ImportError) as exc:
                        status, body = 'rejected', {'error': str(exc)[:2048]}
                    writer.write(encode_frame({'version': 1, 'generation': self.location.generation,
                        'cwd': str(self.location.cwd), 'status': status, 'body': body}))
                    await writer.drain()
            except (ValueError, OSError, TimeoutError):
                pass
            finally:
                if work is not None:
                    if not work.done():
                        work.cancel()
                    await asyncio.gather(work, return_exceptions=True)
                    self._controls.discard(work)
                if gone is not None:
                    gone.cancel()
                    await asyncio.gather(gone, return_exceptions=True)
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
                writers.discard(writer)
                if shutdown and not self.entries and not self._pending_starts:
                    self.stop.set()

        tasks: set[asyncio.Task[None]] = set()

        def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            if self._closing:
                writer.close()
                return
            writers.add(writer)
            task = asyncio.create_task(connection(reader, writer))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        try:
            path = self.location.socket
            if path.exists() or path.is_symlink():
                validate_socket(self.location)
                info = path.lstat()
                try:
                    async with asyncio.timeout(0.5):
                        reader, writer = await asyncio.open_unix_connection(path)
                except ConnectionRefusedError:
                    unlink_owned(path, (info.st_dev, info.st_ino))
                else:
                    writer.close()
                    await writer.wait_closed()
                    raise RuntimeError('Eyes address still has a live listener; refusing replacement')
            server = await asyncio.start_unix_server(accept, path=str(path), limit=MAX_FRAME, start_serving=False)
            info = path.lstat()
            self._socket_identity = info.st_dev, info.st_ino
            os.chmod(path, 0o600)
            publish = asyncio.create_task(asyncio.to_thread(publish_location, self.location))
            cancelled = False
            while not publish.done():
                try:
                    await asyncio.shield(publish)
                except asyncio.CancelledError:
                    cancelled = True
            self._record_identity = publish.result()
            if cancelled:
                raise asyncio.CancelledError
            await server.start_serving()
            monitor = asyncio.create_task(self.monitor())
            await self.stop.wait()
        finally:
            async def cleanup() -> None:
                self._closing = True
                try:
                    if server is not None:
                        server.close()
                    for writer in tuple(writers):
                        writer.close()
                    pending = set(self._controls) | set(tasks) | set(self._starts)
                    if monitor is not None:
                        pending.add(monitor)
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    failures = []
                    for identifier in tuple(self.entries):
                        try:
                            await self.stop_entry(identifier)
                        except BaseException as exc:
                            failures.append(exc)
                    if server is not None:
                        await server.wait_closed()
                    if failures:
                        raise failures[0]
                finally:
                    unlink_owned(self.location.socket, self._socket_identity)
                    unlink_owned(self.location.directory / 'owner.json', self._record_identity)
                    if self._lease >= 0:
                        os.close(self._lease)
                        self._lease = -1

            closing = asyncio.create_task(cleanup())
            cancelled = False
            while not closing.done():
                try:
                    await asyncio.shield(closing)
                except asyncio.CancelledError:
                    cancelled = True
            closing.result()
            if cancelled:
                raise asyncio.CancelledError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cwd', required=True)
    options = parser.parse_args()

    async def run() -> None:
        from pathlib import Path

        runtime = ObserverRuntime(runtime_location(Path(options.cwd)))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, runtime.stop.set)
        try:
            await runtime.serve()
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)

    asyncio.run(run())


if __name__ == '__main__':
    main()
