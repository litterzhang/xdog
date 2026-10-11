"""Once-only reviewed host work owned by a real worker callback, not a UI grant."""
from __future__ import annotations

import asyncio
import contextvars
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

Commit = Callable[[], Awaitable[None]]
Discard = Callable[[], None]
_current: contextvars.ContextVar[InvocationLease | None] = contextvars.ContextVar('extension_invocation', default=None)


@dataclass
class InvocationLease:
    """Internal host lease. Only the runtime may complete it; extensions never receive it.

    A commit must recheck its own host revision/capability and report its own outcome.
    Queue acceptance is not application or persistence. A raised/uncertain commit is
    never replayed and does not roll back already admitted work.
    """
    extension: str
    session_id: str
    child_id: str
    method: str
    owner_alive: Callable[[], bool]
    owner_key: object = field(repr=False)
    parent: InvocationLease | None = field(default=None, repr=False)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _pending: list[tuple[Commit, Discard | None]] = field(default_factory=list, repr=False)
    _open: bool = field(default=True, repr=False)

    def defer(self, commit: Commit, discard: Discard | None = None) -> bool:
        """Register at most eight explicit proposals; never run a callback at admission."""
        if (not self._open or not self.owner_alive() or len(self._pending) >= 8
                or not callable(commit) or discard is not None and not callable(discard)):
            return False
        self._pending.append((commit, discard))
        return True

    async def complete(self, success: bool) -> None:
        if not self._open:
            return
        self._open = False
        pending, self._pending = self._pending, []  # Consume before awaiting; no replay on cancellation.
        task = asyncio.current_task()
        accepted = success and self.owner_alive() and not (task is not None and task.cancelling())
        for index, (commit, discard) in enumerate(pending):
            if not accepted:
                if discard is not None:
                    discard()
                continue
            if self.parent is not None:
                if not self.parent.defer(commit, discard) and discard is not None:
                    discard()
                continue
            try:
                if not self.owner_alive():
                    if discard is not None:
                        discard()
                else:
                    await commit()
            except BaseException:
                # The current action may have effects. Only still-unadmitted actions
                # are discarded; no automatic retry or imaginary rollback.
                for _, remaining_discard in pending[index + 1:]:
                    if remaining_discard is not None:
                        remaining_discard()
                raise


def current_invocation() -> InvocationLease | None:
    value = _current.get()
    return value if value is not None and value._open and value.owner_alive() else None
