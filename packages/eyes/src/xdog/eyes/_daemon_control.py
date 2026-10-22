"""Private Eyes CLI/tool control implementation, not a public embedding SDK.

Only finite JSON crosses the Unix socket.

``events`` delivery never opens an ACP connection. Consumers persist their own
business effect before cumulative ``ack``; subscriptions never auto-acknowledge.
An owner is a namespace within one OS user's authority, not an authentication token.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast

from xdog.eyes.control import request
from xdog.eyes.loader import definition_data
from xdog.eyes.local_control import JsonValue, RuntimeLocation, daemon_location, object_value, read_location, text
from xdog.eyes.models import ObserverDef
from xdog.eyes.process import ensure_runtime
from xdog.eyes.protocol import require_compatible


class DaemonUnavailable(ConnectionError):
    """Only read-side reconnection is safe without an explicit mutator retry."""


class DaemonControl:
    def __init__(self, *, owner: str, directory: Path | None = None) -> None:
        self.owner = text(owner)
        self.directory = directory
        self.location: RuntimeLocation | None = None
        self.closed = False
        self._negotiated = ''

    @classmethod
    async def open(cls, *, owner: str, directory: Path | None = None) -> DaemonControl:
        client = cls(owner=owner, directory=directory)
        await client.start_daemon()
        return client

    async def start_daemon(self) -> dict[str, JsonValue]:
        if self.closed:
            raise RuntimeError('Eyes client is closed')
        location = await asyncio.to_thread(daemon_location, self.directory)
        self.location = await asyncio.to_thread(ensure_runtime, location)
        return await self.daemon_status()

    async def _request(self, action: str, body: dict[str, JsonValue], *, scoped: bool = True) -> dict[str, Any]:
        if self.closed:
            raise RuntimeError('Eyes client is closed')
        if self.location is None:
            self.location = await asyncio.to_thread(daemon_location, self.directory)
        if self._negotiated != self.location.generation or not self._negotiated:
            response = await request(replace(self.location, generation=''), 'hello', {}, timeout=5)
            require_compatible(response, daemon=True)
            self.location = replace(self.location, generation=text(response['generation']))
            self._negotiated = self.location.generation
        if scoped:
            body = {**body, 'owner': self.owner}
        response = await request(self.location, action, body, timeout=120)
        result = object_value(response['body'])
        if response['status'] != 'stale-generation':
            self.location = replace(self.location, generation=text(response['generation']))
        if response['status'] == 'stale-generation':
            raise DaemonUnavailable('Eyes daemon generation changed')
        if response['status'] not in {'ok', 'accepted', 'duplicate'}:
            raise ValueError(str(result.get('error', 'Eyes daemon generation changed; reopen explicitly')))
        if response['status'] == 'duplicate':
            result['duplicate'] = True
        return result

    async def daemon_status(self) -> dict[str, JsonValue]:
        return await self._request('daemon-status', {}, scoped=False)

    async def stop_daemon(self) -> dict[str, JsonValue]:
        """Explicit whole-instance shutdown; not called by aclose."""
        try:
            result = await self._request('daemon-stop', {}, scoped=False)
        except (FileNotFoundError, ConnectionRefusedError):
            return {'state': 'not-running', 'shutdown': True}
        assert self.location is not None
        original = self.location
        async with asyncio.timeout(30):
            while True:
                try:
                    current = await asyncio.to_thread(read_location, original)
                except FileNotFoundError:
                    break
                if current.generation != original.generation:
                    break  # Never wait on, or stop, a replacement generation.
                await asyncio.sleep(.02)
        return result

    async def start(self, definition: ObserverDef | dict[str, Any], *,
                    delivery: Literal['events', 'handlers'] = 'handlers',
                    key: str | None = None, recover: bool = True, wait_ready: bool = True,
                    timeout: float = 30.0,
                    source_checkpoints: dict[str, JsonValue] | None = None) -> dict[str, Any]:
        # Dict input must already be definition_data(parse(..., base_dir=the_json_parent)).
        # The daemon has no definition-file location or management workspace to infer a base.
        value = definition_data(definition) if isinstance(definition, ObserverDef) else definition
        body: dict[str, JsonValue] = {'definition': cast(dict[str, JsonValue], value),
                                     'delivery': delivery, 'recover': recover}
        if source_checkpoints is not None:
            body['source_checkpoints'] = source_checkpoints
        if key is not None:
            body['key'] = text(key)
        result = await self._request('register', body)
        if wait_ready:
            duplicate = result['duplicate']
            result = await self.wait_ready(result['registration_id'], timeout=timeout)
            result['duplicate'] = duplicate
        return result

    async def register(self, definition: ObserverDef | dict[str, Any], **options: Any) -> dict[str, Any]:
        return await self.start(definition, **options)

    async def wait_ready(self, identifier: str, *, timeout: float = 30.0) -> dict[str, Any]:
        try:
            async with asyncio.timeout(timeout):
                while True:
                    try:
                        result = await self.status(identifier)
                    except (ConnectionError, FileNotFoundError, TimeoutError):
                        self.location = None  # read owner generation again; never start or mutate
                    else:
                        if result['state'] == 'watching':
                            return result
                        if result['state'] not in {'running', 'baselining', 'needs-start'}:
                            raise ValueError('Eyes registration not ready: ' + str(result))
                    await asyncio.sleep(.05)
        except TimeoutError as exc:
            raise TimeoutError(f'Eyes registration {identifier} not ready within {timeout:g}s') from exc

    async def status(self, identifier: str | None = None) -> dict[str, Any]:
        return await self._request('owner-status', {'registration_id': identifier} if identifier is not None else {})

    async def pause(self, identifier: str) -> dict[str, Any]:
        return await self._request('pause', {'registration_id': identifier})

    async def resume(self, identifier: str) -> dict[str, Any]:
        return await self._request('owner-resume', {'registration_id': identifier})

    async def cancel(self, identifier: str) -> dict[str, Any]:
        return await self._request('cancel', {'registration_id': identifier})

    async def stop(self, identifier: str) -> dict[str, Any]:
        return await self.cancel(identifier)

    async def events(self, *, after: int | None = None, limit: int = 64, wait: float = 0.0) -> list[dict[str, Any]]:
        if isinstance(wait, bool) or not 0 <= wait <= 120:
            raise ValueError('Expected wait seconds 0..120')
        deadline = time.monotonic() + wait
        body: dict[str, JsonValue] = {'limit': limit}
        if after is not None:
            body['after'] = after
        while True:
            response = await self._request('events', body)
            rows = cast(list[dict[str, Any]], response['events'])
            if rows or time.monotonic() >= deadline:
                return rows
            await asyncio.sleep(min(.1, max(0., deadline-time.monotonic())))

    async def ack(self, sequence: int) -> dict[str, JsonValue]:
        return await self._request('ack', {'sequence': sequence})

    async def subscribe(self, *, after: int | None = None, limit: int = 64) -> AsyncIterator[dict[str, Any]]:
        delay = .1
        while not self.closed:
            try:
                rows = await self.events(after=after, limit=limit, wait=1)
            except (ConnectionError, FileNotFoundError, TimeoutError):
                # No mutation, including daemon startup, is performed by follow.
                # Only reload the original instance's current owner generation.
                await asyncio.sleep(delay)
                delay = min(5., delay * 2)
                try:
                    location = await asyncio.to_thread(daemon_location, self.directory)
                    self.location = await asyncio.to_thread(read_location, location)
                except (ConnectionError, FileNotFoundError, TimeoutError):
                    pass
                continue
            delay = .1
            for event in rows:
                yield event
                # Connection-local read cursor only. Durable ack stays explicit.
                after = event['sequence']

    async def aclose(self) -> None:
        self.closed = True

    async def __aenter__(self) -> DaemonControl:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
