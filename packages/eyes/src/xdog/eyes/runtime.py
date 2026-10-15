"""Independent session-bound observation process; no model/harness imports."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from xdog.eyes.events import RuntimeEvent
from xdog.eyes.executor import Registration
from xdog.eyes.handlers.session_notify import SessionNotifier
from xdog.eyes.loader import parse
from xdog.eyes.models import ObserverDef
from xdog.eyes.transport.client import HostClient
from xdog.eyes.transport.protocol import (
    MAX_FRAME,
    HostContext,
    JsonValue,
    Owner,
    ProtocolError,
    decode_frame,
    encode_frame,
    load_context,
    object_value,
    text,
)


def definition_data(definition: ObserverDef) -> dict[str, JsonValue]:
    return object_value(json.loads(json.dumps(asdict(definition), default=str, allow_nan=False)))


@dataclass
class Entry:
    registration: Registration
    notifier: SessionNotifier
    digest: str
    state: str
    sources: dict[str, str]
    error: str = ""
    delivery_paused: bool = False


class ObserverRuntime:
    def __init__(self, context: HostContext, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.clock, self.sleep = clock, sleep
        self.context = context
        self.client = HostClient(context)
        self.entries: dict[str, Entry] = {}
        self.names: dict[str, str] = {}
        self.stop = asyncio.Event()
        self.lock = asyncio.Lock()
        self._started = self.clock()

    def context_alive(self) -> bool:
        try:
            current = load_context(self.context.descriptor)
            return (current.owner == self.context.owner
                    and secrets.compare_digest(current.token.encode(), self.context.token.encode()))
        except ProtocolError:
            return False

    def emit(self, identifier: str, event: RuntimeEvent) -> None:
        entry = self.entries.get(identifier)
        if entry is None:
            return
        if event.kind in {"baselining", "paused", "stopped"}:
            entry.state = event.kind
        if event.kind == "watching":
            entry.sources[event.source_id] = "watching"
            if (len(entry.sources) == len(entry.registration.definition.sources)
                    and all(state == "watching" for state in entry.sources.values()) and not entry.delivery_paused):
                entry.state, entry.error = "watching", ""
        elif event.kind == "source_error":
            entry.sources[event.source_id] = "error"
            if not entry.delivery_paused:
                entry.state = "degraded"
        if event.kind == "paused":
            entry.delivery_paused = True
        elif event.kind == "delivery_accepted":
            entry.delivery_paused = False
            if all(state == "watching" for state in entry.sources.values()):
                entry.state, entry.error = "watching", ""
        if event.error:
            entry.error = event.error
        path = self.context.directory / "eyes-events.jsonl"
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_size >= 1024 * 1024:
                os.ftruncate(fd, 0)
            os.write(fd, json.dumps(asdict(event), ensure_ascii=True).encode() + b"\n")
        finally:
            os.close(fd)

    async def start(self, data: object) -> dict[str, JsonValue]:
        # V2 must never be sent through the retired capability/register/deliver
        # path. Task 9 replaces this runtime with Eyes-owned ACP lifecycle.
        parse(data, base_dir=Path("/"))
        raise RuntimeError("Eyes v2 requires the independent ACP runtime; legacy host activation is removed")

    async def stop_entry(self, identifier: str) -> dict[str, JsonValue]:
        async with self.lock:
            entry = self.entries.pop(identifier, None)
            if entry is not None:
                self.names.pop(entry.registration.definition.name, None)
                await entry.registration.aclose()
                try:
                    await entry.notifier.unregister()
                except (OSError, ProtocolError, TimeoutError):
                    pass  # Local observation has stopped even if the host already retired.
        return {"registration_id": identifier, "stopped": True}

    async def status(self, identifier: str | None = None) -> dict[str, JsonValue]:
        rows: list[JsonValue] = []
        for key, entry in self.entries.items():
            if identifier is not None and key != identifier:
                continue
            task = entry.registration.task
            if task.done() and not task.cancelled() and task.exception() is not None:
                entry.state, entry.error = "failed", str(task.exception())
            rows.append({"registration_id": key, "name": entry.registration.definition.name,
                         "state": entry.state, "sources": dict(entry.sources), "error": entry.error})
        body: dict[str, JsonValue] = {"registrations": rows}
        try:
            response = await self.client.request({"action": "status", "body":
                                                 {"registration_id": identifier} if identifier else {}})
            body["host"] = response["body"]
        except (OSError, ProtocolError, TimeoutError):
            body["host"] = {"state": "unavailable"}
        return body

    async def dispatch(self, value: object) -> tuple[str, dict[str, JsonValue]]:
        keys = {"version", "action", "token", "owner", "body"}
        data = object_value(value, allowed=keys, required=keys)
        if type(data["version"]) is not int or data["version"] != 1:
            raise ProtocolError("Unsupported control protocol")
        if not await asyncio.to_thread(self.context_alive):
            self.stop.set()
            return "stale-owner", {}
        if Owner.parse(data["owner"]) != self.context.owner:
            return "stale-owner", {}
        if not secrets.compare_digest(text(data["token"]).encode(), self.context.token.encode()):
            return "rejected", {}
        action = text(data["action"])
        if action == "hello":
            object_value(data["body"], allowed=set())
            return "ok", {}
        if action == "start":
            body = object_value(data["body"], allowed={"definition"}, required={"definition"})
            return "accepted", await self.start(body["definition"])
        if action in {"stop", "resume", "status"}:
            body = object_value(data["body"], allowed={"registration_id"},
                                required=set() if action == "status" else {"registration_id"})
            identifier = text(body["registration_id"]) if "registration_id" in body else None
            if action == "status":
                return "ok", await self.status(identifier)
            assert identifier is not None
            if action == "stop":
                return "accepted", await self.stop_entry(identifier)
            if identifier not in self.entries:
                return "rejected", {"error": "Unknown active registration"}
            response = await self.client.request({"action": "resume", "body": {"registration_id": identifier}})
            if response["status"] == "accepted":
                self.entries[identifier].registration.resume.set()
            return text(response["status"]), object_value(response["body"])
        raise ProtocolError("Unknown runtime control action")

    async def monitor(self) -> None:
        confirmed, next_ping = self.clock(), 0.0
        while not self.stop.is_set():
            now = self.clock()
            if not await asyncio.to_thread(self.context_alive):
                self.stop.set()
                break
            if now - confirmed >= 15:
                self.stop.set()
                break
            if now >= next_ping:
                next_ping = now + 5
                try:
                    response = await asyncio.wait_for(self.client.request({"action": "ping", "body": {}}),
                                                      timeout=min(5.0, 15 - (now - confirmed)))
                    if response["status"] == "ok":
                        confirmed = self.clock()
                    elif response["status"] in {"stale-owner", "rejected"}:
                        self.stop.set()
                except (OSError, ProtocolError, TimeoutError):
                    pass
            if self.clock() - confirmed >= 15 or (not self.entries and now - self._started >= 15):
                self.stop.set()
            if not self.stop.is_set():
                await self.sleep(0.2)

    async def serve(self) -> None:
        loop = asyncio.get_running_loop()
        tasks: set[asyncio.Task[None]] = set()
        writers: set[asyncio.StreamWriter] = set()

        async def connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            action = ""
            shutdown_after_reply = False
            try:
                async with asyncio.timeout(5):
                    data = decode_frame(await reader.readline())
                action = text(data.get("action"))
                async with asyncio.timeout(125 if action == "resume" else 10):
                    try:
                        status, body = await self.dispatch(data)
                        shutdown_after_reply = action == "stop" and status == "accepted"
                    except (ValueError, RuntimeError, OSError) as exc:
                        status, body = "rejected", {"error": str(exc)}
                    writer.write(encode_frame({"version": 1, "status": status, "body": body}))
                    await writer.drain()
            except (OSError, ValueError, TimeoutError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
                writers.discard(writer)
                if shutdown_after_reply and not self.entries:
                    self.stop.set()

        def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            if self.stop.is_set():
                writer.close()
                return
            writers.add(writer)
            task = asyncio.create_task(connection(reader, writer))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        path = self.context.directory / "eyes.sock"
        server = await asyncio.start_unix_server(connected, path=path, limit=MAX_FRAME)
        monitor = asyncio.create_task(self.monitor())
        try:
            await self.stop.wait()
        finally:
            for listener in server.sockets or ():
                loop.remove_reader(listener.fileno())
            await asyncio.sleep(0)
            server.close()
            for writer in tuple(writers):
                writer.close()
            monitor.cancel()
            connections = tuple(tasks)
            for task in connections:
                task.cancel()
            await asyncio.gather(monitor, *connections, return_exceptions=True)
            await server.wait_closed()
            for identifier in tuple(self.entries):
                await self.stop_entry(identifier)
            await self.client.aclose()
            path.unlink(missing_ok=True)
            record = self.context.directory / "eyes-runtime.json"
            try:
                value = json.loads(record.read_text())
                if value.get("pid") == os.getpid():
                    record.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    options = parser.parse_args()
    asyncio.run(ObserverRuntime(load_context(options.context)).serve())


if __name__ == "__main__":
    main()
