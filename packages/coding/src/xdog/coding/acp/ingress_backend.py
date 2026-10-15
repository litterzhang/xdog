"""Model-free ACP ingress: durable intake or routing to an existing owner only."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar, cast

from acp import PromptResponse, RequestError
from acp.schema import TextContentBlock
from xdog.agent.acp.contracts import AcpUpdate, JsonValue, Peer, SessionRef, UpdateSink, finite_json, invalid
from xdog.coding.acp.client import AcpAttachment
from xdog.coding.core.sessions.inbox_models import label, message_from_data
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.coding.core.sessions.ownership import OwnerRegistry

T = TypeVar("T")


class IngressBackend:
    extensions = {'enqueue': 1, 'inbox_status': 1, 'session_info': 1, 'resume': 1}

    def __init__(self, store: InboxStore, registry: OwnerRegistry) -> None:
        self.store, self.registry = store, registry
        self._remote: dict[tuple[str, str], AcpAttachment] = {}
        self._refs: dict[tuple[str, str], SessionRef] = {}
        self._busy: set[tuple[str, str]] = set()
        self._loading: set[tuple[str, str]] = set()
        self._disconnected: set[str] = set()
        self._workers: set[asyncio.Task[Any]] = set()
        self._shutting_down = False

    async def _disk(self, operation: Callable[..., T], *args: object) -> T:
        # Cancelling an RPC does not stop its OS thread. Retain that receipt
        # until publication/lock cleanup settles before the service can stop.
        if self._shutting_down:
            raise invalid("ACP ingress peer is shutting down")
        worker = asyncio.create_task(asyncio.to_thread(operation, *args))
        self._workers.add(worker)
        worker.add_done_callback(self._workers.discard)
        cancelled = False
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancelled = True
        result = worker.result()
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def new_session(self, cwd: Path, peer: Peer) -> SessionRef:
        raise invalid('ACP ingress never creates or restores an Agent/session owner')

    async def load_session(self, session_id: str, cwd: Path, peer: Peer, emit: UpdateSink) -> SessionRef:
        key = peer.id, session_id
        if peer.id in self._disconnected or key in self._busy or key in self._loading:
            raise invalid('ACP peer/session is closed or busy')
        self._loading.add(key)
        attachment = None
        try:
            record = await self._disk(self.registry.lookup, session_id)
            if record.cwd.resolve() != cwd:
                raise invalid('Session workspace mismatch')
            attachment = AcpAttachment(record)
            async with asyncio.timeout(10):
                await attachment.connect(emit)
            if peer.id in self._disconnected:
                raise invalid('ACP peer disconnected during load')
            previous = self._remote.pop(key, None)
            if previous is not None:
                await previous.close()
            self._remote[key] = attachment
            ref = SessionRef(session_id, cwd, record.generation)
            self._refs[key] = ref
            return ref
        except BaseException as exc:
            if attachment is not None:
                await attachment.close()
            if isinstance(exc, (ValueError, OSError, RuntimeError)):
                raise invalid('Session owner inactive/unavailable: ' + str(exc)) from exc
            raise
        finally:
            self._loading.discard(key)

    def _bound(self, ref: SessionRef, peer: Peer) -> AcpAttachment:
        key = peer.id, ref.id
        if peer.id in self._disconnected or self._refs.get(key) != ref or key not in self._remote:
            raise invalid('Unknown or disconnected owner binding')
        return self._remote[key]

    @staticmethod
    async def _validate(ref: SessionRef, remote: AcpAttachment) -> None:
        if remote.conn is None:
            raise invalid('Owner connection is closed')
        info = await remote.conn.ext_method('xdog/session_info', {'session_id': ref.id})
        if (info.get('session_id') != ref.id or info.get('generation') != ref.generation
                or info.get('cwd') != str(ref.cwd)):
            raise invalid('Session owner context changed; explicit reattach required')

    async def prompt(self, ref: SessionRef, peer: Peer, text: str,
                     metadata: dict[str, JsonValue], emit: UpdateSink) -> PromptResponse:
        remote = self._bound(ref, peer)
        key = peer.id, ref.id
        if key in self._busy:
            raise invalid('A prompt is already pending for this peer/session')
        self._busy.add(key)
        try:
            async with asyncio.timeout(10):
                await self._validate(ref, remote)
            return await self._stream(remote, ref, text, metadata, emit)
        finally:
            self._busy.discard(key)

    async def _stream(self, remote: AcpAttachment, ref: SessionRef, text: str,
                      metadata: dict[str, JsonValue], emit: UpdateSink) -> PromptResponse:
        queue: asyncio.Queue[tuple[AcpUpdate, int]] = asyncio.Queue(maxsize=256)
        fault: asyncio.Future[BaseException] = asyncio.get_running_loop().create_future()
        buffered = 0

        async def receive(update: AcpUpdate) -> None:
            nonlocal buffered
            size = len(update.model_dump_json().encode())
            if queue.full() or buffered + size > 1024 * 1024:
                if not fault.done():
                    fault.set_result(RuntimeError('ACP ingress output exceeds capacity'))
                return
            buffered += size
            queue.put_nowait((update, size))

        async def drain() -> None:
            nonlocal buffered
            try:
                while True:
                    update, size = await queue.get()
                    try:
                        await emit(update)
                    finally:
                        buffered -= size
                        queue.task_done()
            except Exception as exc:
                if not fault.done():
                    fault.set_result(exc)

        async def checked(task: asyncio.Task[Any]) -> Any:
            await asyncio.wait({task, fault}, return_when=asyncio.FIRST_COMPLETED)
            if fault.done():
                raise fault.result()
            return await task

        connection = remote.conn
        if connection is None:
            raise invalid('Owner connection closed')
        remote.sink = receive
        consumer = asyncio.create_task(drain())
        request = asyncio.create_task(connection.prompt(
            session_id=ref.id, prompt=[TextContentBlock(type='text', text=text)], **metadata))
        join = None
        try:
            result = await checked(request)
            join = asyncio.create_task(queue.join())
            await checked(join)
            return PromptResponse.model_validate(result)
        except BaseException:
            await remote.close()  # Revokes only this forwarded ACP peer, never another owner.
            raise
        finally:
            remote.sink = None
            tasks = [consumer, request] + ([join] if join is not None else [])
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel(self, ref: SessionRef, peer: Peer) -> None:
        remote = self._bound(ref, peer)
        if remote.conn is not None:
            await remote.conn.cancel(session_id=ref.id)

    async def disconnect(self, peer: Peer) -> None:
        self._disconnected.add(peer.id)
        self._shutting_down = True
        if self._workers:
            settlement = asyncio.gather(*tuple(self._workers), return_exceptions=True)
            cancelled = False
            while not settlement.done():
                try:
                    await asyncio.shield(settlement)
                except asyncio.CancelledError:
                    cancelled = True
            settlement.result()
            if cancelled:
                raise asyncio.CancelledError
        for key, remote in tuple(self._remote.items()):
            if key[0] == peer.id:
                self._remote.pop(key, None)
                self._refs.pop(key, None)
                await remote.close()

    async def extension(self, peer: Peer, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if peer.id in self._disconnected:
            raise invalid('ACP peer is closed')
        try:
            finite_json(params)
            if method == 'xdog/enqueue':
                message = message_from_data(params)
                receipt = await self._disk(self.store.enqueue, message)
                return {'session_id': message.session_id, 'message_id': message.message_id,
                        'status': 'duplicate' if receipt.duplicate else 'queued',
                        'message_state': receipt.record.status, 'branch_id': receipt.record.branch_id}
            if method == 'xdog/inbox_status':
                if not isinstance(params, dict) or set(params) - {'session_id', 'cwd', 'message_id', 'cursor'}:
                    raise ValueError('Invalid inbox status fields')
                session_id, cwd = label(params.get('session_id')), Path(label(params.get('cwd'), maximum=4096))
                if not cwd.is_absolute():
                    raise ValueError('Inbox status workspace must be absolute')
                selected = label(params['message_id']) if 'message_id' in params else None
                cursor = params.get('cursor', 0)
                if (type(cursor) is not int or not 0 <= cursor <= 4096
                        or (selected is not None and 'cursor' in params)):
                    raise ValueError('Invalid inbox status cursor/selection')
                rows = await self._disk(self.store.records, session_id, cwd)
                working, branch = await self._disk(self.store.identity, session_id, cwd)
                if selected is not None:
                    rows = tuple(row for row in rows if row.message.message_id == selected)
                page = rows[cursor:cursor + 64]
                return {'session_id': session_id, 'cwd': str(working), 'branch_id': branch,
                        'messages': [{'message_id': r.message.message_id, 'status': r.status,
                                      'branch_id': r.branch_id, 'attempt_id': r.attempt_id,
                                      'request_id': r.request_id} for r in page],
                        'next_cursor': cursor + len(page) if cursor + len(page) < len(rows) else None}
            if method not in {'xdog/session_info', 'xdog/resume'}:
                raise RequestError(-32601, 'Unsupported ACP ingress extension')
            if set(params) != {'session_id'}:
                raise ValueError('Expected bound session_id')
            session_id = label(params['session_id'])
            ref = self._refs.get((peer.id, session_id))
            if ref is None:
                raise ValueError('Unknown bound owner session')
            remote = self._bound(ref, peer)
            await self._validate(ref, remote)
            return cast(dict[str, Any], await remote.conn.ext_method(method, params))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise invalid(str(exc)) from exc
        except OSError as exc:
            raise RequestError(-32000, 'Inbox/owner unavailable; receipt not acknowledged: ' + str(exc)) from exc
