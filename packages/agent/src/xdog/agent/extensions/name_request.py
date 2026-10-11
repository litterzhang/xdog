"""Reviewed parent-run name CAS: callback success is not producer settlement."""
from __future__ import annotations

import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from xdog.agent.extensions.invocation import current_invocation
from xdog.agent.session_name import session_name

METHODS = frozenset({'tools/call', 'xdog/pre', 'xdog/post', 'xdog/input', 'xdog/event', 'xdog/context_finish'})


def available(method: str, params: dict[str, Any]) -> bool:
    return (method in METHODS
        and (method != 'xdog/context_finish' or params.get('operation', 'context') == 'context')
        and (method != 'xdog/event' or params.get('event') not in {'session_before_compact', 'session_compact'}))


@dataclass
class PendingName:
    id: str
    extension: str
    session_id: str
    name: str
    expected_name: str
    producer: object = field(repr=False)
    alive: Callable[[], bool] = field(repr=False)
    settled: Callable[[], bool] = field(repr=False)
    successful: Callable[[], bool] = field(repr=False)
    staged: bool = False


class AfterRunNames:
    """One proposal/review per owning host. Hosts alone supply the real producer barrier.

    No task is started here. ``take`` cannot release an unapproved, failed callback,
    unsettled producer, changed source, or failed/canceled producer. Host execution
    must additionally recheck ``alive`` in its canonical name commit path.
    """
    def __init__(self, publish: Callable[[dict[str, Any]], None] = lambda event: None) -> None:
        self._pending: PendingName | None = None
        self._lock = threading.RLock()
        self.publish = publish

    def _notice(self, item: PendingName, status: str) -> None:
        self.publish({'type': 'extension_name_after_run', 'request_id': item.id,
                      'session_id': item.session_id, 'extension': item.extension, 'status': status})

    def discard(self, reason: str = 'discarded', *, item: PendingName | None = None) -> None:
        with self._lock:
            current = self._pending
            if current is None or item is not None and current is not item:
                return
            self._pending = None
        self._notice(current, reason)

    async def reviewed(self, extension: str, dialog: dict[str, Any], *, producer: object,
                       ask: Callable[[str, dict[str, Any]], Awaitable[Any]], alive: Callable[[], bool],
                       settled: Callable[[], bool], successful: Callable[[], bool]) -> bool:
        lease = current_invocation()
        if (lease is None or lease.extension != extension or lease.child_id or lease.method not in METHODS
                or not alive() or settled() or dialog.get('kind') != 'session'
                or dialog.get('operation') != 'rename' or dialog.get('name_after_run') is not True
                or 'name_run' in dialog):
            return False
        name, expected_name = session_name(dialog['name']), session_name(dialog['expected_name'])
        item = PendingName('extname-' + uuid.uuid4().hex, extension, lease.session_id, name, expected_name,
            producer, lambda: lease.owner_alive() and alive(), settled, successful)
        with self._lock:
            if self._pending is not None:
                return False
            self._pending = item
        staged = False
        try:
            projected = {**dialog, 'name_run': {'id': item.id, 'session_id': lease.session_id,
                         'method': lease.method}, 'message':
                f'Session: {lease.session_id} · callback: {lease.method}\n'
                'Original parent run only. After this callback succeeds AND its entire producer settles, '
                'save this same session name through the normal host CAS pipeline. Any cancellation, failure, '
                'new producer or changed owner discards the proposal. Never rename a parent for a child.'}
            if await ask(extension, projected) is not True or not item.alive() or settled():
                return False
            async def commit() -> None:
                with self._lock:
                    if self._pending is item and item.alive() and not settled():
                        item.staged = True
                    else:
                        self.discard(item=item)
                        return
                self._notice(item, 'staged')
            staged = lease.defer(commit, lambda: self.discard(item=item))
            return staged
        finally:
            if not staged:
                self.discard(item=item)

    def take(self) -> PendingName | None:
        with self._lock:
            item = self._pending
            if item is None:
                return None
            if not item.alive():
                self.discard(item=item)
                return None
            if not item.settled():
                return None
            self._pending = None
        if not item.staged or not item.successful():
            self._notice(item, 'discarded')
            return None
        self._notice(item, 'ready')
        return item
