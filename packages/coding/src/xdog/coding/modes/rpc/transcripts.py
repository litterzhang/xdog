"""Connection-owned complete transcript snapshots; read-only, not session or tool authority."""
from __future__ import annotations

import asyncio
import base64
import re
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from xdog.agent import AgentMessage
from xdog.agent.extensions.runtime import _owned_disk
from xdog.agent.supervision import ChildCapture
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.transcript_source import _wire_messages, source_records
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.tui.child_browser import child_records
from xdog.tui.source_spool import SourceSpool, spool_records

SOURCE_BLOCK_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SourceRequest:
    identifier: str
    session_id: str
    session_revision: str
    revision: int
    manager: SessionManager
    target: str
    branch: str
    scope: str
    messages: tuple[AgentMessage, ...] | None
    partial: AgentMessage | None
    compaction: Any
    active_branch: str
    child: ChildCapture | None = None
    child_owner: str = ''
    child_epoch: str = ''
    alive: Callable[[], bool] | None = None
    format: str = "display"

    def envelope(self) -> dict[str, Any]:
        value: dict[str, Any] = {'request_id': self.identifier, 'session_id': self.session_id,
                'session_revision': self.session_revision, 'revision': self.revision,
                'group_id': '', 'target_session_id': self.target, 'branch_id': self.branch, 'scope': self.scope}
        if self.format != 'display':
            value['format'] = self.format
        if self.child is not None:
            value.update(child_id=self.child.id, child_owner=self.child_owner, child_epoch=self.child_epoch,
                         child_revision=self.child.revision)
        return value


def prepare(request: SourceRequest, canceled: threading.Event) -> SourceSpool:
    if request.format == 'canonical':
        from xdog.coding.core.canonical_source import spool_canonical, spool_child_canonical
        if request.child is not None:
            return spool_child_canonical(request.child, request.target, canceled, max_bytes=MAX_SOURCE_BYTES)
        return spool_canonical(request.manager, request.target, request.branch, messages=request.messages,
            partial=request.partial, active_branch=request.active_branch, compaction=request.compaction,
            canceled=canceled, max_bytes=MAX_SOURCE_BYTES)
    if request.child is not None:
        _wire_messages(request.child.messages)
        if request.child.partial is not None:
            _wire_messages((request.child.partial,))
        records = child_records(request.child.to_dict())
    else:
        records = source_records(request.manager, request.target, request.branch,
            live_messages=request.messages, partial=request.partial, live_compaction=request.compaction,
            live_branch=request.active_branch, canceled=canceled)
    return spool_records(records, canceled, max_bytes=MAX_SOURCE_BYTES)


class RpcTranscripts:
    def __init__(self, session: AgentSession, navigation: RpcNavigation,
                 emit: Callable[[dict[str, Any]], None], *,
                 send: Callable[[dict[str, Any]], Awaitable[None]] | None = None) -> None:
        self.session, self.navigation, self.emit = session, navigation, emit
        self.send = send
        self.enabled = False
        self.canonical_enabled = False
        self.closed = False
        self.used: set[str] = set()
        self.pending: SourceRequest | None = None
        self.active: SourceRequest | None = None
        self.task: asyncio.Task[None] | None = None
        self.canceled = threading.Event()
        self.reason = 'Transcript read canceled'

    def negotiate(self, packet: dict[str, Any]) -> None:
        if 'transcript_canonical' in packet:
            value = packet['transcript_canonical']
            self.canonical_enabled = type(value) is int and value == 1
            if not self.canonical_enabled:
                if (self.pending is not None and self.pending.child is None
                        and self.pending.format == 'canonical'):
                    self._error(self.pending, 'Canonical capability withdrawn', canceled=True)
                    self.pending = None
                if (self.active is not None and self.active.child is None
                        and self.active.format == 'canonical'):
                    self.reason = 'Canonical capability withdrawn'
                    self.canceled.set()
        if 'transcript_sources' in packet:
            value = packet['transcript_sources']
            self.enabled = type(value) is int and value == 1
            if not self.enabled:
                self._cancel_all('Transcript capability withdrawn', children=False)
        self.emit({'type': 'transcript_capabilities', 'transcript_sources': int(self.enabled),
                   'transcript_canonical': int(self.canonical_enabled),
                   'max_source_bytes': MAX_SOURCE_BYTES, 'source_block_bytes': SOURCE_BLOCK_BYTES})

    def _error(self, request: SourceRequest, reason: str, *, canceled: bool = False) -> None:
        if not self.closed:
            self.emit({'type': 'source_error', **request.envelope(), 'error': reason, 'canceled': canceled})

    def _cancel_all(self, reason: str, *, children: bool = True) -> None:
        if self.pending is not None and (children or self.pending.child is None and self.pending.format == 'display'):
            self._error(self.pending, reason, canceled=True)
            self.pending = None
        if children or self.active is not None and self.active.child is None and self.active.format == 'display':
            self.reason = reason
            self.canceled.set()

    def cancel_children(self, owner: str, *, format: str | None = None) -> None:
        if (self.pending is not None and self.pending.child_owner == owner
                and (format is None or self.pending.format == format)):
            self._error(self.pending, 'Child source capability/owner withdrawn', canceled=True)
            self.pending = None
        if (self.active is not None and self.active.child_owner == owner
                and (format is None or self.active.format == format)):
            self.reason = 'Child source capability/owner withdrawn'
            self.canceled.set()

    def start(self, packet: dict[str, Any]) -> None:
        key, owner, revision = (packet.get(name) for name in ('request_id', 'session_id', 'session_revision'))
        format = packet.get('format', 'display')
        if not isinstance(format, str) or format not in {'display', 'canonical'}:
            raise ValueError('Transcript format must be display or canonical')
        enabled = self.canonical_enabled if format == 'canonical' else self.enabled
        if not enabled or self.closed:
            raise ValueError('Negotiate the exact transcript format capability before reading content')
        if (not isinstance(key, str) or not key or len(key.encode()) > 128 or key in self.used
                or len(self.used) >= 256 or owner != self.session.session_id or revision != self.navigation.revision):
            raise ValueError('Invalid/stale/reused transcript request owner or connection capacity')
        if self.navigation.busy:
            raise RuntimeError('Session navigation owns the context; read after its actual receipt')
        scope = packet.get('scope', 'live')
        target, branch = packet.get('target_session', owner), packet.get('branch_id', '')
        if not isinstance(scope, str) or scope not in {'live', 'saved', 'timeline'}:
            raise ValueError('Transcript scope must be live, saved or timeline')
        for value in (target, branch):
            if (not isinstance(value, str) or len(value) > 128
                    or value and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value) is None):
                raise ValueError('Invalid transcript session/branch identity')
        if not target or scope == 'live' and (target != owner or branch) or scope == 'timeline' and branch:
            raise ValueError('Transcript scope cannot silently substitute another source')
        state = self.session.agent.state
        live = scope == 'live' or scope == 'timeline' and target == owner
        self.used.add(key)
        request = SourceRequest(key, owner, revision, len(self.used), self.session.session_manager, target,
            '@timeline' if scope == 'timeline' else branch, scope,
            tuple(state.messages) if live else None, state.stream_message if scope == 'live' else None,
            self.session.session_data.compaction, self.session.session_data.active_branch, format=format)
        self._queue(request)

    def start_child(self, packet: dict[str, Any], captured: ChildCapture, *, owner: str, epoch: str,
                    alive: Callable[[], bool]) -> None:
        key = packet.get('request_id')
        format = packet.get('format', 'display')
        if not isinstance(format, str) or format not in {'display', 'canonical'}:
            raise ValueError('Child transcript format must be display or canonical')
        if (self.closed or not alive() or not isinstance(key, str) or not key or len(key.encode()) > 128
                or key in self.used or len(self.used) >= 256):
            raise ValueError('Invalid/stale/reused child source owner or request capacity')
        self.used.add(key)
        request = SourceRequest(key, self.session.session_id, self.navigation.revision, len(self.used),
            self.session.session_manager, self.session.session_id, '', 'child', None, None, None, '',
            captured, owner, epoch, alive, format=format)
        self._queue(request)

    def _queue(self, request: SourceRequest) -> None:
        if self.pending is not None:
            self._error(self.pending, 'Superseded by a newer transcript read', canceled=True)
        self.pending = request
        self.reason = 'Superseded by a newer transcript read'
        self.canceled.set()
        self.emit({'type': 'transcript_queued', **request.envelope()})
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._work())

    def cancel(self, packet: dict[str, Any]) -> None:
        key = packet.get('request_id')
        request = next((item for item in (self.pending, self.active) if item is not None
                        and item.identifier == key and packet.get('session_id') == item.session_id
                        and packet.get('session_revision') == item.session_revision), None)
        if request is None:
            raise ValueError('Unknown/stale transcript cancel owner')
        if request.child is not None and any(packet.get(key) != value for key, value in
                (('child_id', request.child.id), ('child_owner', request.child_owner),
                 ('child_epoch', request.child_epoch))):
            raise ValueError('Child source cancellation owner/epoch changed')
        if request is self.pending:
            self.pending = None
            self._error(request, 'Transcript read canceled before preparation', canceled=True)
        else:
            self.reason = 'Transcript read canceled'
            self.canceled.set()
        self.emit({'type': 'transcript_cancel_requested', **request.envelope()})

    def _guard(self, request: SourceRequest, canceled: threading.Event) -> None:
        enabled = self.canonical_enabled if request.format == 'canonical' else self.enabled
        if self.closed or canceled.is_set() or request.child is None and not enabled:
            raise ValueError(self.reason)
        if (request.alive is not None and not request.alive()
                or self.session.session_id != request.session_id
                or self.navigation.revision != request.session_revision):
            raise ValueError('Transcript owner/context changed; no live fallback or replay')

    async def _send(self, event: dict[str, Any]) -> None:
        if self.send is not None:
            await self.send(event)
        else:
            self.emit(event)
            await asyncio.sleep(0)

    async def _work(self) -> None:
        while self.pending is not None and not self.closed:
            request, self.pending = self.pending, None
            self.active = request
            self.canceled = canceled = threading.Event()
            spool: SourceSpool | None = None
            preparing: asyncio.Task[SourceSpool] | None = None
            try:
                self._guard(request, canceled)
                preparing = asyncio.create_task(asyncio.to_thread(prepare, request, canceled))
                spool = await asyncio.shield(preparing)
                self._guard(request, canceled)
                await self._send({'type': 'source_begin', **request.envelope(), 'bytes': spool.size,
                           'sha256': spool.sha256})
                index = 0
                while True:
                    self._guard(request, canceled)
                    block = await _owned_disk(spool.file.read, SOURCE_BLOCK_BYTES)
                    self._guard(request, canceled)
                    if not block:
                        await self._send({'type': 'source_end', **request.envelope(), 'chunks': index})
                        break
                    await self._send({'type': 'source_chunk', **request.envelope(), 'index': index,
                               'data': base64.b64encode(block).decode('ascii')})
                    index += 1
                    await asyncio.sleep(0)  # The control/approval reader retains scheduling ownership.
            except asyncio.CancelledError:
                canceled.set()
                raise
            except Exception:
                reason = self.reason if canceled.is_set() else 'Transcript source unavailable/changed; no fallback'
                self._error(request, reason, canceled=canceled.is_set())
            finally:
                canceled.set()
                if preparing is not None and spool is None:
                    # File work is not forcibly cancelable: settle/reap its one owned
                    # spool before starting another request or disposing the session.
                    while not preparing.done():
                        try:
                            await asyncio.shield(preparing)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    if not preparing.cancelled() and preparing.exception() is None:
                        spool = preparing.result()
                try:
                    if spool is not None:
                        await _owned_disk(spool.file.close)
                finally:
                    self.active = None

    async def aclose(self) -> None:
        self.closed = True
        self.pending = None
        self.canceled.set()
        if self.task is not None:
            if not self.task.done():
                self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
