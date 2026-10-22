"""Independent observation lifecycle; ACP is loaded only when explicitly activating a definition."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import os
import signal
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from xdog.eyes.handlers.contracts import EventHandler
from xdog.eyes.handlers.registry import HandlerRegistry
from xdog.eyes.local_control import (
    MAX_FRAME,
    ControlError,
    JsonValue,
    RuntimeLocation,
    daemon_location,
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
from xdog.eyes.models import HandlerDef, SourceDef
from xdog.eyes.service import ObserverService
from xdog.eyes.sources.file import FileSnapshot, scan_async


class ObserverRuntime(ObserverService):
    stop: asyncio.Event  # type: ignore[assignment]

    def __init__(self, location: RuntimeLocation, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 scanner: Callable[[SourceDef], Awaitable[FileSnapshot]] = scan_async,
                 handler_factory: Callable[[HandlerDef], EventHandler] | None = None,
                 handler_registry: HandlerRegistry | None = None,
                 wall_clock: Callable[[], float] = time.time) -> None:
        validate_location(location)
        self.location = replace(location, generation=uuid.uuid4().hex)
        super().__init__(cwd=location.cwd, clock=clock, sleep=sleep, scanner=scanner,
                         handler_factory=handler_factory, handler_registry=handler_registry,
                         wall_clock=wall_clock, auto_health=False)
        from xdog.eyes.daemon import DaemonObservers
        self.daemon = (DaemonObservers(location, clock=clock, wall_clock=wall_clock, sleep=sleep)
                       if location.cwd == location.directory else None)
        self.stop = self._shutdown
        self._controls: set[asyncio.Task[Any]] = set()
        self._since = self.clock()
        self._lease = -1
        self._socket_identity: tuple[int, int] | None = None
        self._record_identity: tuple[int, int] | None = None
        self._closing = False

    async def monitor(self) -> None:
        while not self.stop.is_set():
            if (self.daemon is None and not self.entries and not self._pending_starts
                    and self.clock() - self._since >= 10):
                self.stop.set()
                break
            if self.daemon is not None:
                await self.daemon.health()
            else:
                await self.health()
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
            from xdog.eyes.protocol import hello

            return 'ok', hello(daemon=self.daemon is not None)
        if self.daemon is not None:
            if action == 'daemon-stop':
                object_value(body, allowed=set())
                return 'accepted', {'shutdown': True}
            return await self.daemon.dispatch(action, body)
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
                'notice': 'Resume recovers data delivery only; it never requests execution permission'}
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
        listener = None
        staged = self.location.directory / ('.eyes-' + uuid.uuid4().hex[:12] + '.sock')
        staged_identity = None
        monitor = recovery = None

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
                        if self.daemon is None:
                            work.cancel()
                        # Durable accepted controls belong to the daemon, not the
                        # connection. ACK loss cannot undo registration/cancel.
                        status, body = await asyncio.shield(work)
                        shutdown = frame.get('action') == 'daemon-stop' and body.get('shutdown') is True
                        return
                    try:
                        status, body = work.result()
                        shutdown = frame.get('action') in {'stop', 'daemon-stop'} and body.get('shutdown') is True
                    except (ValueError, RuntimeError, OSError, ImportError) as exc:
                        status, body = 'rejected', {'error': str(exc)[:2048]}
                    writer.write(encode_frame({'version': 1, 'generation': self.location.generation,
                        'cwd': str(self.location.cwd), 'status': status, 'body': body}))
                    await writer.drain()
            except (ValueError, OSError, RuntimeError, ImportError, TimeoutError):
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
            if self.daemon is not None:
                await self.daemon.open()
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
            # Never expose the canonical address with bind's umask-derived mode.
            # Configure the inode under the existing 0700 directory before any await.
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(staged))
            info = staged.lstat()
            staged_identity = info.st_dev, info.st_ino
            os.chmod(staged, 0o600)
            server = await asyncio.start_unix_server(accept, sock=listener, limit=MAX_FRAME, start_serving=False)
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
            # Same-directory link publication is atomic and refuses an occupied path.
            # The linked socket is already private, listening and generation-bound.
            os.link(staged, path)
            self._socket_identity = staged_identity
            unlink_owned(staged, staged_identity)
            if self.daemon is not None:
                recovery = asyncio.create_task(self.daemon.recover(), name='eyes-recovery')
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
                    if recovery is not None:
                        pending.add(recovery)
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    if self.daemon is not None:
                        await self.daemon.aclose()
                    await self.aclose()
                    if server is not None:
                        await server.wait_closed()
                finally:
                    if listener is not None:
                        listener.close()
                    unlink_owned(staged, staged_identity)
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
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--cwd')
    group.add_argument('--directory')
    options = parser.parse_args()

    async def run() -> None:
        from pathlib import Path

        runtime = ObserverRuntime(daemon_location(Path(options.directory)) if options.directory
                                  else runtime_location(Path(options.cwd)))
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
