"""Owned local event service. The network thread never runs an agent turn."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import tempfile
import threading
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from xdog.coding.core.external_events.inbox import ExternalEventInbox
from xdog.coding.core.external_events.protocol import (
    MAX_FRAME,
    JsonValue,
    Owner,
    ProtocolError,
    decode_frame,
    encode_frame,
    object_value,
    text,
    validate_request,
)


class ExternalEventHost:
    def __init__(
        self, inbox: ExternalEventInbox, *, scope: Callable[[], tuple[str, str]],
        directory: Path | None = None, approve: Callable[[str], Awaitable[bool]] | None = None,
        notify: Callable[[], None] | None = None,
    ) -> None:
        self.inbox = inbox
        self._scope = scope
        self._approve = approve
        self._notify = notify or (lambda: None)
        self.directory = directory or Path(tempfile.mkdtemp(prefix="xdog-events-"))
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = self.directory.lstat()
        if self.directory.is_symlink() or metadata.st_uid != os.getuid() or metadata.st_mode & 0o777 != 0o700:
            raise ValueError("Event directory must be private and owned by the current user")
        self.address = self.directory / "host.sock"
        if len(os.fsencode(self.address)) >= 108:
            raise ValueError("Event socket path exceeds the local Unix socket limit")
        self._instance = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._owner = self._new_owner()
        self.descriptor = self.directory / f"context-{self._owner.generation}.json"
        self._token = secrets.token_hex(32)
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._error: BaseException | None = None
        self._closed = False

    @property
    def generation(self) -> str:
        with self._lock:
            return self._owner.generation

    def _new_owner(self) -> Owner:
        session, branch = self._scope()
        return Owner(self._instance, session, branch, uuid.uuid4().hex)

    def _write_descriptor(self) -> None:
        temporary = self.directory / ("context-" + uuid.uuid4().hex + ".tmp")
        value: dict[str, JsonValue] = {"version": 1, "address": str(self.address),
                                      "token": self._token, "owner": self._owner.as_json()}
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, ensure_ascii=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Event host is closed")
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="xdog-external-events", daemon=True)
            self._thread.start()
        if not self._ready.wait(5):
            self.close()
            raise TimeoutError("Event host did not become ready")
        if self._error is not None:
            self.close()
            raise RuntimeError("Could not start local event host") from self._error

    def rotate(self) -> None:
        with self._lock:
            if self._closed:
                return
            self.inbox.invalidate()
            previous = self.descriptor
            self._owner = self._new_owner()
            self.descriptor = self.directory / f"context-{self._owner.generation}.json"
            self._token = secrets.token_hex(32)
            self._write_descriptor()
            previous.unlink(missing_ok=True)
        self._notify()

    def _authorization(self, request: dict[str, JsonValue]) -> str | None:
        if self._closed or Owner.parse(request["owner"]) != self._owner:
            return "stale-owner"
        if self._scope() != (self._owner.session_id, self._owner.branch_id):
            return "stale-owner"
        supplied = text(request["token"]).encode("utf-8")
        if not secrets.compare_digest(supplied, self._token.encode("ascii")):
            return "rejected"
        return None

    async def _dispatch(self, request: dict[str, JsonValue]) -> tuple[str, dict[str, JsonValue]]:
        body = object_value(request["body"])
        action = text(request["action"])
        with self._lock:
            refused = self._authorization(request)
            if refused:
                return refused, {}
            if action in {"hello", "ping"}:
                return "ok", {"external_events": 1}
            if action == "register":
                status = self.inbox.register(text(body["registration_id"]), text(body["route_id"]),
                                             text(body["instruction"], maximum=65536))
            elif action == "unregister":
                self.inbox.unregister(text(body["registration_id"]))
                status = "accepted"
            elif action == "deliver":
                status = self.inbox.accept(request)
            elif action == "status":
                identifier = text(body["registration_id"]) if "registration_id" in body else None
                return "ok", self.inbox.snapshot(identifier)
            else:
                status = "resume-review"
        if action == "resume":
            identifier = text(body["registration_id"])
            allowed = await self._approve(identifier) if self._approve is not None else False
            with self._lock:
                refused = self._authorization(request)
                if refused:
                    return refused, {}
                status = self.inbox.resume(identifier, approved=allowed)
        self._notify()
        return status, {}

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        connections: set[asyncio.Task[None]] = set()
        writers: set[asyncio.StreamWriter] = set()

        async def connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                try:
                    async with asyncio.timeout(5):
                        message = validate_request(decode_frame(await reader.readline()))
                    async with asyncio.timeout(120 if message["action"] == "resume" else 5):
                        status, body = await self._dispatch(message)
                except (ProtocolError, ValueError):
                    status, body = "rejected", {}
                async with asyncio.timeout(5):
                    writer.write(encode_frame({"version": 1, "status": status, "body": body}))
                    await writer.drain()
            except (OSError, TimeoutError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
                finally:
                    writers.discard(writer)

        def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            if self._closed:
                writer.close()
                return
            writers.add(writer)
            task = asyncio.create_task(connection(reader, writer))
            connections.add(task)
            task.add_done_callback(connections.discard)

        server = await asyncio.start_unix_server(connected, path=self.address, limit=MAX_FRAME)
        try:
            with self._lock:
                self._write_descriptor()
                self._ready.set()
                if self._closed:
                    self._stop.set()
            await self._stop.wait()
        finally:
            # Stop new accepts, then let already scheduled transport creation attach
            # before Server.close() changes its ownership bookkeeping (3.12/3.13).
            for listener in server.sockets or ():
                self._loop.remove_reader(listener.fileno())
            await asyncio.sleep(0)
            server.close()
            for writer in tuple(writers):
                writer.close()
            tasks = tuple(connections)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await server.wait_closed()

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self._error = exc
            self._ready.set()
        finally:
            self.address.unlink(missing_ok=True)
            self.descriptor.unlink(missing_ok=True)
            try:
                self.directory.rmdir()
            except OSError:
                pass  # Other provider-owned runtime files may still be retiring.

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.inbox.invalidate()
            self.descriptor.unlink(missing_ok=True)
            loop, stop, thread = self._loop, self._stop, self._thread
        if loop is not None and stop is not None:
            try:
                loop.call_soon_threadsafe(stop.set)
            except RuntimeError:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Event host did not settle during shutdown")
