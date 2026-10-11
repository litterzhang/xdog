"""Reviewed original-parent tool selection; callback success is not the idle CAS barrier."""
from __future__ import annotations

import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from xdog.agent.extensions.active_tools import answer, names
from xdog.agent.extensions.invocation import current_invocation

METHODS = frozenset({'tools/call', 'xdog/pre', 'xdog/post', 'xdog/input', 'xdog/event', 'xdog/context_finish'})
_COMMON = {'id', 'kind', 'operation', 'title', 'message', 'timeout', 'extension', 'parent_id'}
_HOST = {'current_tools', 'tool_selection_run', 'active_tools_after_run_generation'}


def available(method: str, params: dict[str, Any]) -> bool:
    return (method in METHODS
        and (method != 'xdog/context_finish' or params.get('operation', 'context') == 'context'))


def request(value: dict[str, Any], *, projected: bool = True) -> None:
    allowed = _COMMON | {'names', 'revision', 'after_run'} | (_HOST if projected else set())
    if (value.keys() - allowed or value.get('after_run') is not True or 'names' not in value
            or type(value.get('revision')) is not int or value['revision'] < 0):
        raise ValueError('After-run tool selection cannot choose another scope or supply host review fields')
    names(value['names'])
    if 'current_tools' in value:
        answer(value['current_tools'])
    if 'tool_selection_run' in value:
        scope = value['tool_selection_run']
        if (not isinstance(scope, dict) or set(scope) != {'id', 'session_id', 'method'}
                or not isinstance(scope['id'], str) or not 0 < len(scope['id']) <= 128
                or not isinstance(scope['session_id'], str) or not 0 < len(scope['session_id'].encode()) <= 256
                or not isinstance(scope['method'], str) or scope['method'] not in METHODS):
            raise ValueError('Invalid after-run tool selection identity')
    if 'active_tools_after_run_generation' in value:
        generation = value['active_tools_after_run_generation']
        if type(generation) is not int or not 0 <= generation <= 2**53:
            raise ValueError('Invalid after-run tool selection generation')


@dataclass
class PendingToolSelection:
    id: str
    extension: str
    session_id: str
    names: tuple[str, ...] | None
    revision: int
    current_tools: dict[str, Any]
    producer: object = field(repr=False)
    alive: Callable[[], bool] = field(repr=False)
    settled: Callable[[], bool] = field(repr=False)
    successful: Callable[[], bool] = field(repr=False)
    staged: bool = False


class AfterRunToolSelections:
    """Use the existing host producer markers/barriers; never spawn a loop or mutate tools.

    Hosts supply the same original-thread/task completion proof used for after-run
    compaction. Taking a proposal does not apply it: the host must run its existing
    idle durable selection CAS with this revision and recheck ``alive``.
    """
    def __init__(self, publish: Callable[[dict[str, Any]], None] = lambda event: None) -> None:
        self._pending: PendingToolSelection | None = None
        self._lock = threading.RLock()
        self.publish = publish

    def _notice(self, item: PendingToolSelection, status: str) -> None:
        self.publish({'type': 'extension_active_tools_after_run', 'request_id': item.id,
                      'session_id': item.session_id, 'extension': item.extension, 'status': status})

    def discard(self, reason: str = 'discarded', *, item: PendingToolSelection | None = None) -> None:
        with self._lock:
            current = self._pending
            if current is None or item is not None and current is not item:
                return
            self._pending = None
        self._notice(current, reason)

    async def reviewed(self, extension: str, dialog: dict[str, Any], *, producer: object,
                       ask: Callable[[str, dict[str, Any]], Awaitable[Any]], current: dict[str, Any],
                       alive: Callable[[], bool], settled: Callable[[], bool],
                       successful: Callable[[], bool]) -> bool:
        lease = current_invocation()
        if (lease is None or lease.extension != extension or lease.child_id or lease.method not in METHODS
                or not alive() or settled() or dialog.get('kind') != 'session'
                or dialog.get('operation') != 'select_tools' or dialog.get('after_run') is not True):
            return False
        request(dialog, projected=False)
        captured = answer(current)
        selected = names(dialog.get('names'))
        revision = dialog.get('revision')
        if (type(revision) is not int or revision != captured['revision']
                or captured['session_id'] != lease.session_id
                or selected is not None and not set(selected) <= set(captured['configured'])):
            return False
        item = PendingToolSelection('exttools-' + uuid.uuid4().hex, extension, lease.session_id,
            selected, revision, captured, producer, lambda: lease.owner_alive() and alive(), settled, successful)
        with self._lock:
            if self._pending is not None:
                return False
            self._pending = item
        staged = False
        try:
            projected = {**dialog, 'current_tools': answer(captured),
                'tool_selection_run': {'id': item.id, 'session_id': lease.session_id, 'method': lease.method},
                'message': f'Session: {lease.session_id} · callback: {lease.method}\n'
                    'Review these exact configured/current/requested names. Only after this callback succeeds AND '
                    'the entire original parent producer settles successfully may the host apply its ordinary idle '
                    'durable selection CAS. Changed inventory, owner, revision, failed/canceled producer, or a new '
                    'producer discards this proposal. No mid-tool-batch changes, tool permissions or child grants.'}
            if await ask(extension, projected) is not True or not item.alive() or settled():
                return False
            async def stage() -> None:
                with self._lock:
                    if self._pending is item and item.alive() and not settled():
                        item.staged = True
                    else:
                        self.discard(item=item)
                        return
                self._notice(item, 'staged')
            staged = lease.defer(stage, lambda: self.discard(item=item))
            return staged
        finally:
            if not staged:
                self.discard(item=item)

    def take(self) -> PendingToolSelection | None:
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
