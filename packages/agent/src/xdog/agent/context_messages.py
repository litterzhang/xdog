"""Durable original-session/branch next-prompt context; no scheduling or model calls."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from xdog.agent.core import AgentMessage, CustomAgentMessage
from xdog.agent.extensions.context_message import TYPE, record, reviewed

T = TypeVar('T')
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class ContextAdmissionRejected(ValueError):
    """Known pre-write model/owner refusal, not uncertain disk mutation."""


class ContextMessages:
    """Pending canonical IDs retire only after history persistence.

    Original-owner disk work is joined on cancellation. Pre-replace cancellation
    refuses mutation; post-replace errors are uncertain, never rolled back/replayed.
    Same-path adapters share a nonblocking lock (plus POSIX process ownership).
    """

    def __init__(self, root: Path, *, scope: Callable[[], tuple[str, str]],
                 history: Callable[[], tuple[AgentMessage, ...]],
                 persist: Callable[[tuple[AgentMessage, ...]], Awaitable[None]],
                 eligible: Callable[[], bool] = lambda: True,
                 publish: Callable[[dict[str, Any]], None] = lambda _event: None) -> None:
        self.root, self.scope, self.history, self.persist = root, scope, history, persist
        self.eligible, self.publish = eligible, publish
        self._uncertain_scope: tuple[str, str] | None = None
        self.deliver: Callable[..., Awaitable[None]] | None = None
        self.running: Callable[[], bool] = lambda: False

    @property
    def uncertain(self) -> bool:
        return self._uncertain_scope == self.scope()

    @uncertain.setter
    def uncertain(self, value: bool) -> None:
        self._uncertain_scope = self.scope() if value else None

    def _path(self, scope: tuple[str, str]) -> Path:
        return self.root / (hashlib.sha256(json.dumps(scope).encode()).hexdigest() + '.json')

    @contextmanager
    def _ownership(self, scope: tuple[str, str]) -> Iterator[None]:
        path = self._path(scope)
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(str(path.absolute()), threading.Lock())
        if not lock.acquire(blocking=False):
            raise RuntimeError('Another context message transaction owns this session/branch')
        descriptor = None
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(path.with_suffix('.lock'),
                                 os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            import fcntl
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another process owns context message persistence') from None
            yield
        finally:
            if descriptor is not None:
                os.close(descriptor)
            lock.release()

    async def _disk(self, work: Callable[[Callable[[], bool], threading.Event], T], *,
                    alive: Callable[[], bool] = lambda: True, receipt: dict[str, Any] | None = None) -> T:
        original_scope = self.scope()
        canceled, replaced = threading.Event(), threading.Event()
        task = asyncio.create_task(asyncio.to_thread(work, lambda: not canceled.is_set() and alive(), replaced))
        try:
            return await asyncio.shield(task)
        except BaseException as error:
            canceled.set()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except (asyncio.CancelledError, Exception):
                    continue
            if not task.cancelled():
                task.exception()  # Observe late failures; no success publication after cancellation.
            if replaced.is_set():
                self._uncertain_scope = original_scope
            self.publish({'type': 'extension_context_persistence', **(receipt or {}),
                          'status': 'uncertain' if replaced.is_set() else
                          'canceled' if isinstance(error, asyncio.CancelledError) else 'failed'})
            raise

    def _load(self, scope: tuple[str, str]) -> list[dict[str, Any]]:
        path = self._path(scope)
        if path.is_symlink():
            raise ValueError('Context message queue cannot be a symlink')
        if not path.exists():
            return []
        if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Invalid/excessive context message queue')
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or set(value) != {'scope', 'pending'} or value['scope'] != list(scope):
            raise ValueError('Context message queue owner mismatch')
        rows = value['pending']
        if not isinstance(rows, list) or len(rows) > 32:
            raise ValueError('Invalid context message queue')
        output = [record(row) for row in rows]
        if (len({row['id'] for row in output}) != len(output)
                or any((row['session_id'], row['branch_id']) != scope for row in output)):
            raise ValueError('Duplicate/foreign context message queue entry')
        return output

    def _write(self, scope: tuple[str, str], rows: list[dict[str, Any]],
               current: Callable[[], bool], replaced: threading.Event) -> None:
        encoded = json.dumps({'scope': list(scope), 'pending': rows}, ensure_ascii=True, allow_nan=False)
        if len(rows) > 32 or len(encoded.encode()) > 16 * 1024 * 1024:
            raise ValueError('Context message queue capacity exceeded')
        path = self._path(scope)
        if path.is_symlink():
            raise ValueError('Context message queue cannot be a symlink')
        fd, name = tempfile.mkstemp(prefix='.context-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if not current() or self.scope() != scope:
                raise ValueError('Context message canceled/owner changed before atomic replacement')
            os.replace(name, path)
            replaced.set()
            directory = os.open(self.root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    async def enqueue(self, value: dict[str, Any], alive: Callable[[], bool]) -> None:
        value = record(value)
        scope = value['session_id'], value['branch_id']
        def save(current: Callable[[], bool], replaced: threading.Event) -> None:
            with self._ownership(scope):
                if not current() or self.scope() != scope:
                    raise ValueError('Context message original owner changed before commit')
                rows = self._load(scope)
                if not any(row['id'] == value['id'] for row in rows):
                    self._write(scope, [*rows, value], current, replaced)
        await self._disk(save, alive=alive, receipt={key: value[key] for key in
                                                   ('id', 'session_id', 'branch_id', 'invocation')})
        self.publish({'type': 'extension_context_pending', 'id': value['id'], 'extension': value['extension'],
                      'custom_type': value['custom_type'], 'session_id': scope[0], 'branch_id': scope[1]})

    async def review(self, extension: str, dialog: dict[str, Any], *, ask: Any,
                     alive: Callable[[], bool], schedule: Any = None, trigger: Any = None) -> bool:
        scope = self.scope()
        def current() -> bool:
            return alive() and self.scope() == scope
        from xdog.agent.extensions.context_message import validate
        value = validate(dialog)
        immediate = value['delivery'] != 'next_turn'
        running = self.running()
        from xdog.agent.extensions.invocation import current_invocation
        lease = current_invocation()
        if lease is None:
            return False
        if immediate and (self.deliver is None or value.get('trigger_turn') and not running and trigger is None
                          and lease.method == 'xdog/command'):
            return False
        async def apply(canonical: dict[str, Any]) -> None:
            if canonical['delivery'] == 'next_turn':
                await self.enqueue(canonical, current)
            elif self.deliver is not None:
                if self.running() != running:
                    raise ValueError('Reviewed custom delivery phase changed; no active/idle fallback')
                await self.deliver(canonical, current, trigger)
        return await reviewed(extension, dialog, ask=ask,
            enqueue=apply, scope=scope, alive=current, schedule=schedule)

    async def capture(self) -> tuple[tuple[str, str], tuple[str, ...]]:
        """Freeze IDs before the explicit user's turn_start/input lifecycle hooks."""
        if self.uncertain:
            raise RuntimeError('Context persistence uncertain; reload original session before another prompt')
        scope = self.scope()
        def load(_current: Callable[[], bool], _replaced: threading.Event) -> tuple[str, ...]:
            if not self._path(scope).exists():
                return ()  # No implicit directory/lock creation for ordinary prompts without pending context.
            with self._ownership(scope):
                return tuple(row['id'] for row in self._load(scope))
        return scope, await self._disk(load)

    async def admit(self, snapshot: tuple[tuple[str, str], tuple[str, ...]]) -> None:
        if not self.eligible():
            return
        if self.uncertain:
            raise RuntimeError('Context history persistence uncertain; reload original session')
        scope, captured = snapshot
        history, loop = self.history(), asyncio.get_running_loop()
        if self.scope() != scope:
            raise ValueError('Context message admission belongs to another session/branch')
        known = {message.data.get('id') for message in history
                 if isinstance(message, CustomAgentMessage) and message.type == TYPE}
        async def commit(fresh: list[CustomAgentMessage], current: Callable[[], bool]) -> None:
            if not current() or self.scope() != scope or self.history() != history or not self.eligible():
                raise ValueError('Context history changed before admission')
            try:
                await self.persist((*history, *fresh))
            except ContextAdmissionRejected:
                raise
            except BaseException:
                self._uncertain_scope = scope
                raise
        def deliver(current: Callable[[], bool], replaced: threading.Event) -> list[CustomAgentMessage]:
            with self._ownership(scope):
                rows = self._load(scope)
                admitted = [row for row in rows if row['id'] in captured]
                fresh = [CustomAgentMessage(TYPE, row) for row in admitted if row['id'] not in known]
                if not current():
                    raise ValueError('Context prompt canceled before persistence')
                if fresh:
                    asyncio.run_coroutine_threadsafe(commit(fresh, current), loop).result()
                    replaced.set()  # Canonical history may not be rolled back after this point.
                if admitted:
                    ids = {row['id'] for row in admitted}
                    self._write(scope, [row for row in rows if row['id'] not in ids], current, replaced)
                return fresh
        fresh = await self._disk(deliver, alive=lambda: self.scope() == scope and self.eligible(),
                                 receipt={'session_id': scope[0], 'branch_id': scope[1], 'ids': list(captured)})
        for message in fresh:
            self.publish({'type': 'extension_context_admitted', **message.data})
