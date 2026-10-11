"""Negotiated connection-owned terminal hook jobs, without blocking socket readers."""
from __future__ import annotations

import asyncio
import re
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.terminal_input import event_decision, metadata, text
from xdog.tui.keys import KeyEventType, parse_key_events
from xdog.tui.stdin_buffer import key_tokens


@dataclass(frozen=True, slots=True)
class TerminalTarget:
    extensions: ExtensionRuntime
    alive: Callable[[], bool]


class TerminalInputJobs:
    """One in-flight callback per connection, finite receipts and no retry/replay.

    Each callback captures a concrete active runtime BEFORE task scheduling. A
    socket token and the original session/UI lease own both cancellation and
    response delivery; a foreign connection cannot adopt those requests.
    """

    def __init__(self, capture: Callable[[str, str, int], TerminalTarget],
                 write: Callable[[dict[str, Any]], Awaitable[None]], *, timeout: float = 4,
                 capture_child: Callable[[str, str, int, str, str], TerminalTarget] | None = None) -> None:
        self.capture, self.write, self.timeout = capture, write, timeout
        self.supported = False
        self.child_supported = False
        self.release_supported = False
        self.capture_child = capture_child
        self.owner = uuid.uuid4().hex
        self._closed = False
        self._task: asyncio.Task[None] | None = None
        self._request: dict[str, Any] | None = None
        self._retired: deque[str] = deque(maxlen=128)
        self._last_sequence = 0

    def negotiate(self, version: Any) -> None:
        supported = type(version) is int and version == 1
        if supported != self.supported:
            self.cancel_all()
            self.owner = uuid.uuid4().hex
        self.supported = supported

    def negotiate_child(self, version: Any) -> None:
        supported = type(version) is int and version == 1
        if not supported and self._request is not None and self._request.get("surface") == "child-prompt":
            self.cancel_all()
        self.child_supported = supported

    def negotiate_release(self, version: Any) -> None:
        supported = type(version) is int and version == 1
        if supported != self.release_supported:
            self.cancel_all()
            self.owner = uuid.uuid4().hex
        self.release_supported = supported

    def cancel_all(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def cancel(self, request: dict[str, Any]) -> bool:
        original = self._request
        keys: tuple[str, ...] = ('request_id', 'group_id', 'session_id', 'terminal_owner', 'input_owner',
                'extension_generation', 'input_sequence')
        if original is not None and original.get('surface') == 'child-prompt':
            keys += ('child_id', 'child_owner', 'surface')
        if original is None or any(type(request.get(key)) is not type(original[key])
                                   or request.get(key) != original[key] for key in keys):
            return False
        self.cancel_all()
        return True

    async def request(self, request: dict[str, Any]) -> None:
        # Never echo arbitrary malformed objects, user data or worker errors.
        identifier = request.get('request_id')
        reply: dict[str, Any] = {'type': 'terminal_input_result', 'error': True}
        for key in ('request_id', 'group_id', 'session_id', 'terminal_owner', 'input_owner',
                    'child_id', 'child_owner', 'surface'):
            value = request.get(key)
            if isinstance(value, str):
                try:
                    if len(value.encode()) <= 256:
                        reply[key] = value
                except UnicodeError:
                    pass
        for key in ('extension_generation', 'input_sequence'):
            value = request.get(key)
            if type(value) is int and 0 <= value <= 2**53:
                reply[key] = value
        try:
            if (type(request.get('input_sequence')) is not int
                    or not self._last_sequence < request['input_sequence'] <= 2**53
                    or type(request.get('extension_generation')) is not int
                    or not 0 <= request['extension_generation'] <= 2**53
                    or self._closed or not self.supported or request.get('terminal_owner') != self.owner
                    or not isinstance(identifier, str) or not 0 < len(identifier.encode()) <= 128
                    or any(not isinstance(request.get(key), str) or not 0 < len(request[key].encode()) <= 256
                           for key in ('group_id', 'session_id', 'input_owner'))
                    or request.get('surface') not in ('prompt', 'child-prompt')
                    or request.get('kind') not in ('key', 'paste', 'release')
                    or request.get('kind') == 'release' and not self.release_supported
                    or identifier in self._retired or self._task is not None and not self._task.done()):
                raise ValueError('Invalid/busy terminal input owner')
            data = text(request.get('data'))
            info = metadata({'kind': request['kind'], 'surface': request['surface']})
            if request['surface'] == 'child-prompt':
                child, child_owner = request.get('child_id'), request.get('child_owner')
                if (not self.child_supported or self.capture_child is None
                        or not isinstance(child, str) or re.fullmatch(r'[a-f0-9]{12}', child) is None
                        or not isinstance(child_owner, str) or re.fullmatch(r'[a-f0-9]{32}', child_owner) is None):
                    raise ValueError('Invalid/unsupported child terminal owner')
                target = self.capture_child(request['group_id'], request['session_id'],
                                            request['extension_generation'], child, child_owner)
            else:
                target = self.capture(request['group_id'], request['session_id'], request['extension_generation'])
            if request['kind'] == 'release':
                if 'release' not in target.extensions.terminal_events:
                    raise ValueError('Terminal release observation not registered')
                # Host-specific keymaps are enforced by the native interceptor.
                # At the transport boundary require real releases, never control
                # bytes, protocol reports, a forged key press or protected interrupts.
                tokens = key_tokens(data.encode())
                if not tokens:
                    raise ValueError('Terminal release requires an encoded event')
                for token in tokens:
                    events = parse_key_events(token)
                    if (len(events) != 1 or events[0].event_type is not KeyEventType.RELEASE
                            or events[0].key == 'escape'
                            or events[0].ctrl and events[0].key.lower() in {'c', 'd', 'l', 'z'}):
                        raise ValueError('Invalid/protected terminal release observation')
            if not target.alive():
                raise ValueError('Terminal runtime owner changed')
        except (ValueError, TypeError, UnicodeError, RuntimeError):
            await self.write(reply)
            return
        self._last_sequence = request['input_sequence']
        self._retired.append(identifier)
        # Snapshot bounded metadata only, never arbitrary request fields/data.
        self._request = {key: request[key] for key in (
            'request_id', 'group_id', 'session_id', 'terminal_owner', 'input_owner',
                'extension_generation', 'input_sequence', 'surface')}
        if request['surface'] == 'child-prompt':
            self._request.update(child_id=request['child_id'], child_owner=request['child_owner'])
        owner = self.owner
        async def run() -> None:
            try:
                if (not target.alive() or info['kind'] == 'release'
                        and (not self.release_supported or 'release' not in target.extensions.terminal_events)):
                    raise ValueError('Terminal runtime owner changed before dispatch')
                async with asyncio.timeout(self.timeout):
                    result = event_decision(await target.extensions.terminal_input(data, **info), info['kind'])
                if (not target.alive() or info['kind'] == 'release'
                        and (not self.release_supported or 'release' not in target.extensions.terminal_events)):
                    raise ValueError('Terminal runtime owner changed before delivery')
                active = target.extensions.terminal_input_available
                if type(active) is not bool:
                    raise ValueError('Invalid live terminal hook registry')
                reply.update(error=False, terminal_input_active=active,
                             terminal_events=list(target.extensions.terminal_events),
                             **({'consume': True} if info['kind'] == 'release' else result.to_dict()))
            except asyncio.CancelledError:
                return  # Remote cancellation is not rollback or a retry receipt.
            except Exception:
                pass
            if (not self._closed and self.supported and owner == self.owner
                    and (request['surface'] != 'child-prompt' or self.child_supported)):
                await self.write(reply)
        self._task = asyncio.create_task(run())
        self._task.add_done_callback(self._observe)

    @staticmethod
    def _observe(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    async def close(self) -> None:
        self._closed = True
        self.cancel_all()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
