"""Asynchronous RPC review requests; execution remains in the session controller."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from xdog.coding.core.inbox_commands import inbox_action
from xdog.coding.core.sessions.inbox_models import label

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.modes.rpc.navigation import RpcNavigation
    from xdog.coding.modes.rpc.rpc_mode import EmitFn


class RpcInbox:
    def __init__(self, session: AgentSession, navigation: RpcNavigation, emit: EmitFn) -> None:
        self.session, self.navigation, self.emit = session, navigation, emit
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.used: set[str] = set()
        self.closed = False

    def start(self, packet: dict[str, Any]) -> None:
        allowed = {'type', 'request_id', 'session_id', 'session_revision', 'generation',
                   'action', 'message_id', 'message_ids', 'cursor'}
        runtime = self.session.runtime
        if (self.closed or runtime is None or set(packet) - allowed
                or packet.get('session_id') != self.session.session_id):
            raise ValueError('Invalid/stale inbox session owner or fields')
        self.navigation.prompt_owner(packet)
        generation = runtime.controller.generation
        if packet.get('generation', generation) != generation:
            raise ValueError('Inbox owner generation changed')
        key = label(packet.get('request_id'), maximum=128)
        if key in self.used or len(self.used) >= 256 or len(self.tasks) >= 16:
            raise ValueError('Reused inbox request ID or RPC request capacity exceeded')
        action, cursor = packet.get('action', 'list'), packet.get('cursor', 0)
        if not isinstance(action, str) or action not in {'list', 'show', 'approve', 'retry'}:
            raise ValueError('Invalid inbox action')
        if action == 'approve':
            raw = packet.get('message_ids')
            if not isinstance(raw, list) or not raw or len(raw) > 128 or 'message_id' in packet or cursor:
                raise ValueError('Inbox approve requires explicit message_ids only')
            ids = tuple(label(value) for value in raw)
            if len(set(ids)) != len(ids):
                raise ValueError('Duplicate inbox review selection')
        elif action in {'show', 'retry'}:
            if 'message_ids' in packet or cursor:
                raise ValueError('Inbox show/retry requires exactly one message_id')
            ids = (label(packet.get('message_id')),)
        else:
            if 'message_ids' in packet or 'message_id' in packet:
                raise ValueError('Inbox list does not accept mutation targets')
            ids = ()
        if type(cursor) is not int or not 0 <= cursor <= 4096:
            raise ValueError('Invalid inbox cursor')
        self.used.add(key)
        sid = self.session.session_id

        async def run() -> None:
            response: dict[str, Any] = {'type': 'inbox_result', 'request_id': key, 'session_id': sid,
                                        'generation': generation, 'action': action, 'success': False}
            try:
                if self.session.session_id != sid or runtime.controller.generation != generation:
                    raise ValueError('Inbox context changed before admission')
                response['result'] = await inbox_action(self.session, action, ids, cursor=cursor)
                response['success'] = True
            except asyncio.CancelledError:
                response.update(cancelled=True, message='Review cancelled; inspect inbox before retrying accepted work')
            except (ValueError, RuntimeError, OSError) as exc:
                response['message'] = str(exc)
            if not self.closed:
                self.emit(response)

        task = asyncio.create_task(run())
        self.tasks[key] = task

        def done(completed: asyncio.Task[None]) -> None:
            self.tasks.pop(key, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(done)

    def cancel(self, packet: dict[str, Any]) -> None:
        if (set(packet) - {'type', 'request_id', 'session_id'}
                or packet.get('session_id') != self.session.session_id):
            raise ValueError('Invalid/stale inbox cancellation owner')
        key = label(packet.get('request_id'), maximum=128)
        task = self.tasks.get(key)
        if task is not None:
            task.cancel()
        self.emit({'type': 'inbox_cancel_requested', 'request_id': key,
                   'session_id': self.session.session_id, 'pending': task is not None,
                   'notice': 'Does not cancel another peer or revoke an already accepted turn'})

    async def aclose(self) -> None:
        self.closed = True
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
