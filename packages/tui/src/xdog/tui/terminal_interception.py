"""Bounded asynchronous input arbitration. Hosts provide dispatch, leases and replay policy."""
from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class HeldTerminalInput:
    id: int
    owner: str
    kind: str
    surface: str
    data: str


class TerminalInputQueue:
    """FIFO results never migrate to another owner; original rejected inputs stay inspectable.

    submit() and poll() only enqueue host work. They never block on a hook, write
    terminal bytes, authorize a tool, parse bracketed-paste boundaries or decide
    which UI controls are protected. The hosting client must supply those policies.
    """

    def __init__(self, owner: Callable[[], str], send: Callable[[HeldTerminalInput], bool],
                 deliver: Callable[[HeldTerminalInput, str], None], notice: Callable[[str], None], *,
                 timeout: float = 5, clock: Callable[[], float] = time.monotonic) -> None:
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0.1 <= timeout <= 30:
            raise ValueError("Invalid terminal interception deadline")
        self.timeout, self.clock, self._deadline = timeout, clock, 0.0
        self.owner, self.send, self.deliver, self.notice = owner, send, deliver, notice
        self._queue: deque[HeldTerminalInput] = deque()
        self._active: HeldTerminalInput | None = None
        self._result: tuple[int, dict[str, Any]] | None = None
        self._counter = 0
        self._bytes = 0
        self._closed = False
        self._recovery: deque[HeldTerminalInput] = deque()
        self._recovery_bytes = 0

    @property
    def pending(self) -> int:
        return len(self._queue) + int(self._active is not None)

    @property
    def held(self) -> tuple[HeldTerminalInput, ...]:
        return ((self._active,) if self._active is not None else ()) + tuple(self._queue)

    @property
    def recovery(self) -> tuple[HeldTerminalInput, ...]:
        return tuple(self._recovery)

    def _retain(self, item: HeldTerminalInput, message: str) -> None:
        size = len(item.data.encode())
        while self._recovery and (len(self._recovery) >= 32 or self._recovery_bytes + size > 128 * 1024):
            self._recovery_bytes -= len(self._recovery.popleft().data.encode())
        self._recovery.append(item)
        self._recovery_bytes += size
        self.notice(message)

    def reject(self, item: HeldTerminalInput, message: str) -> None:
        """Keep an original rejected by the host's delivery validation."""
        self._retain(item, message)

    def take_recovery(self, identifier: int) -> HeldTerminalInput | None:
        """Explicit user recovery/discard only; never automatic input replay."""
        for item in self._recovery:
            if item.id == identifier:
                self._recovery.remove(item)
                self._recovery_bytes -= len(item.data.encode())
                return item
        return None

    def submit(self, data: str, *, kind: str, surface: str) -> bool:
        if (self._closed or not isinstance(data, str) or kind not in ('key', 'paste', 'release')
                or not isinstance(surface, str)):
            return False
        try:
            size = len(data.encode('utf-8'))
            if not 0 < len(surface.encode('utf-8')) <= 128:
                return False
        except UnicodeError:
            return False
        if size > 16000:
            return False
        self._counter += 1
        item = HeldTerminalInput(self._counter, self.owner(), kind, surface, data)
        if self.pending >= 32 or self._bytes + size > 128 * 1024:
            self._retain(item, 'Terminal interception queue full; original input not replayed')
            return False
        self._queue.append(item)
        self._bytes += size
        return True

    def accept(self, identifier: int, result: dict[str, Any]) -> bool:
        if self._closed or self._active is None or identifier != self._active.id or self._result is not None:
            return False
        # Snapshot only bounded fields. Never retain arbitrary remote metadata.
        data, consume, error = result.get('data'), result.get('consume', False), result.get('error', False)
        try:
            valid = (type(consume) is bool and type(error) is bool
                     and (data is None or isinstance(data, str) and len(data.encode()) <= 16000)
                     and not set(result) - {'data', 'consume', 'error'}
                     and (self._active.kind != 'release' or 'data' not in result))
        except UnicodeError:
            valid = False
        self._result = identifier, ({'data': data, 'consume': consume, 'error': error} if valid else {'error': True})
        return True

    def poll(self) -> bool:
        if self._closed:
            return False
        changed = False
        if self._active is not None and self._result is None and self.clock() >= self._deadline:
            self._result = self._active.id, {'error': True}
        if self._active is not None and self._result is not None:
            item, result = self._active, self._result[1]
            self._active, self._result = None, None
            self._bytes -= len(item.data.encode())
            changed = True
            if item.owner != self.owner():
                self._retain(item, 'Terminal input owner changed; original input not replayed')
            elif result.get('error'):
                self._retain(item, 'Terminal hook failed; original input not replayed')
            elif result.get('consume') is not True and item.kind != 'release':
                self.deliver(item, item.data if result.get('data') is None else result['data'])
        if self._active is None and self._queue:
            item = self._queue.popleft()
            changed = True
            if item.owner != self.owner():
                self._bytes -= len(item.data.encode())
                self._retain(item, 'Terminal input owner changed before dispatch; original input not replayed')
            else:
                self._active = item
                self._deadline = self.clock() + self.timeout
                try:
                    sent = self.send(item)
                except (ValueError, RuntimeError, OSError):
                    sent = False
                if not sent:
                    self._result = item.id, {'error': True}
        return changed

    def invalidate(self) -> None:
        # A host interrupt/handoff must stay immediate. Late replies to abandoned
        # work are ignored, never reassigned to a new active input or replayed.
        if self._active is not None:
            self._retain(self._active, 'Terminal input interrupted; original input not replayed')
        while self._queue:
            item = self._queue.popleft()
            self._retain(item, 'Terminal input interrupted; original input not replayed')
        self._active, self._result = None, None
        self._bytes = 0

    def close(self) -> None:
        self.invalidate()
        self._closed = True
