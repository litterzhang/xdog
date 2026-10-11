"""Connection-owned elicitation registration/control; no implicit form/browser consent."""
from __future__ import annotations

import asyncio
import re
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from xdog.agent.mcp.elicitation_owner import ElicitationReplyInvalid
from xdog.agent.mcp.manager import McpManager
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.runtime.session import AgentSession


@dataclass(frozen=True)
class _Binding:
    group: str
    session: str
    manager: McpManager
    lease: str
    revoked: threading.Event
    alive: Callable[[], bool]


class ElicitationConnection:
    """Bounded per-connection views and tasks; disconnect settles original work."""
    def __init__(self, orchestrator: Orchestrator, write: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self.orchestrator, self.write = orchestrator, write
        self.supported = False
        self._closed = False
        self._views: dict[tuple[str, str], _Binding] = {}
        self._jobs: set[asyncio.Task[None]] = set()
        self._used: set[str] = set()
        self._closing: set[tuple[str, str]] = set()
        self._withdrawal: asyncio.Task[None] | None = None
        self._loop = asyncio.get_running_loop()
        self._deliveries: set[asyncio.Task[None]] = set()

    def _publish(self, binding: _Binding, view: str, event: dict[str, Any]) -> None:
        def enqueue() -> None:
            if self._closed or not self.supported or not binding.alive():
                return
            async def deliver() -> None:
                if binding.alive() and self.supported:
                    await self.write({'type': 'mcp_elicitation_event', 'group_id': binding.group,
                        'session_id': binding.session, 'view': view, 'event': event})
            if len(self._deliveries) >= 16:
                binding.revoked.set()  # Bounded output admission, no silent private-result release.
                return
            task = asyncio.create_task(deliver())
            self._deliveries.add(task)
            def done(completed: asyncio.Task[None]) -> None:
                self._deliveries.discard(completed)
                if not completed.cancelled():
                    completed.exception()
            task.add_done_callback(done)
        self._loop.call_soon_threadsafe(enqueue)

    async def _enable(self, group: str, expected: str, view: str, server: str, *, tasks: bool = False) -> None:
        runtime = self.orchestrator._runtimes.get(group)
        if runtime is None or self.orchestrator.source_lease(group)[0] != expected:
            raise ValueError('Elicitation group/session changed')
        session = cast(AgentSession, runtime.get_or_create_session(allow_reset=False))
        # Session construction can select/create an active session. Never accept
        # a guessed cold-session identity from the client as review ownership.
        if session.meta.session_id != expected:
            raise ValueError('Elicitation requires current active session; refresh before enabling')
        key = view, server
        if self._closed or not self.supported or key in self._closing:
            raise ValueError('Elicitation connection revoked before enable')
        if key in self._views or len(self._views) >= 8:
            raise ValueError('Elicitation view already enabled/busy')
        if not self.orchestrator.try_reserve_group(group):
            raise ValueError('Elicitation enable requires idle group')
        try:
            session._children_idle()
            scope = self.orchestrator.source_lease(group)
            revoked = threading.Event()
            manager = runtime.mcp
            def alive() -> bool:
                return (not revoked.is_set() and not self._closed and self.supported
                        and runtime._active_session is session and runtime.mcp is manager
                        and (runtime.get_new_session_id() or '', runtime._source_revision) == scope
                        and not session.agent.is_streaming and not session.children.supervisor.active
                        and not session._compacting and not session.extension_dialogs.has_pending)
            binding = _Binding(group, expected, manager, uuid.uuid4().hex, revoked, alive)
            self._views[key] = binding
            try:
                await manager.register_elicitation(server, lease=binding.lease, alive=alive, tasks=tasks,
                    publish=lambda event: self._publish(binding, view, event))
            except BaseException:
                revoked.set()
                self._views.pop(key, None)
                raise
        finally:
            self.orchestrator.release_group_reservation(group)

    async def request(self, value: dict[str, Any]) -> None:
        rid, group, expected, view, server = (value.get(key) for key in
                                            ('request_id', 'group_id', 'session_id', 'view', 'server'))
        action = value.get('action')
        if (any(not isinstance(item, str) or not 0 < len(item.encode()) <= 128
                for item in (rid, group, expected, view, server))
                or re.fullmatch(r'[a-f0-9]{32}', str(view)) is None
                or action not in {'enable', 'answer', 'detach'}
                or 'tasks' in value and (action != 'enable' or type(value['tasks']) is not bool)):
            # Reject invalid ownership without stranding an identifiable native
            # request. Echo bounded strings only; this is never a capability grant.
            correlation = {key: value[key] for key in
                           ('request_id', 'group_id', 'session_id', 'view', 'server', 'action')
                           if isinstance(value.get(key), str) and len(value[key].encode()) <= 128}
            await self.write({'type': 'mcp_elicitation_result', **correlation, 'success': False,
                              'error': 'Invalid elicitation control'})
            return
        assert isinstance(rid, str) and isinstance(group, str) and isinstance(expected, str)
        assert isinstance(view, str) and isinstance(server, str)
        key = view, server
        reply = {'type': 'mcp_elicitation_result', 'request_id': rid, 'view': view,
                 'group_id': group, 'session_id': expected, 'server': server, 'action': action, 'success': False}
        binding = self._views.get(key)
        if action == 'detach':
            if key in self._closing:
                await self.write({**reply, 'success': True, 'notice': 'Elicitation cleanup already owns work'})
                return
            if binding is not None and (binding.group, binding.session) != (group, expected):
                await self.write({**reply, 'error': 'Foreign elicitation view'})
                return
            if binding is not None:
                binding.revoked.set()  # Reader synchronously revokes before scheduling cleanup.
                self._closing.add(key)
        elif (self._closed or not self.supported or self._withdrawal is not None and not self._withdrawal.done()
              or rid in self._used or len(self._used) >= 256
              or len(self._jobs) >= 8):
            await self.write({**reply, 'error': 'Elicitation capability/identity/capacity unavailable'})
            return
        else:
            self._used.add(rid)
        if action == 'detach' and binding is None:
            await self.write({**reply, 'success': True})
            return
        snapshot = dict(value)
        async def run() -> None:
            try:
                if action == 'enable':
                    await self._enable(group, expected, view, server, tasks=snapshot.get('tasks', False))
                    reply.update(success=True,
                                 notice=('Elicitation tasks armed' if snapshot.get('tasks', False)
                                         else 'Elicitation armed')
                                 + '; connect explicitly; each request/reply needs review')
                elif binding is None or (binding.group, binding.session) != (group, expected):
                    raise ValueError('Elicitation view not registered on this connection')
                elif action == 'detach':
                    await binding.manager.detach_elicitation(server, lease=binding.lease)
                    self._views.pop(key, None)
                    reply.update(success=True)
                else:
                    if not binding.alive():
                        raise ValueError('Elicitation owner revoked')
                    response = snapshot.get('response')
                    if (not isinstance(response, dict) or set(response) - {
                            'id', 'phase', 'receipt', 'sha256', 'response'}
                            or any(not isinstance(response.get(field), str) or len(response[field]) > 128
                                   for field in ('id', 'phase', 'receipt', 'sha256'))):
                        raise ValueError('Invalid elicitation review response')
                    await binding.manager.answer_elicitation(server, response['id'], lease=binding.lease,
                        phase=response['phase'], receipt=response['receipt'], sha256=response['sha256'],
                        response=response.get('response'))
                    reply.update(success=True)
            except ElicitationReplyInvalid:
                reply.update(success=True, recoverable=True)  # Fresh owner event, not success/data release.
            except (ValueError, RuntimeError, OSError, TimeoutError):
                reply['error'] = 'Elicitation control refused/failed; no replay'
            finally:
                if action == 'detach':
                    self._closing.discard(key)
            if not self._closed:
                await self.write(reply)
        task = asyncio.create_task(run())
        self._jobs.add(task)
        def done(completed: asyncio.Task[None]) -> None:
            self._jobs.discard(completed)
            if not completed.cancelled():
                completed.exception()
        task.add_done_callback(done)

    def negotiate(self, value: Any) -> None:
        self.supported = type(value) is int and value == 1
        if not self.supported:
            for binding in tuple(self._views.values()):
                binding.revoked.set()
            # Do not await model finally in the socket reader. Admission is
            # synchronously revoked; this bounded task owns eventual settlement.
            if self._withdrawal is None or self._withdrawal.done():
                self._withdrawal = asyncio.create_task(self._detach_all())

    async def _detach_all(self) -> None:
        bindings = tuple(self._views.items())
        for _, binding in bindings:
            binding.revoked.set()
        for task in tuple(self._jobs):
            task.cancel()
        await asyncio.gather(*tuple(self._jobs), return_exceptions=True)
        await asyncio.gather(*(binding.manager.detach_elicitation(key[1], lease=binding.lease)
                              for key, binding in tuple(self._views.items())), return_exceptions=True)
        self._views.clear()

    async def close(self) -> None:
        self._closed = True
        self.supported = False
        if self._withdrawal is not None:
            await self._withdrawal
        await self._detach_all()
        for task in tuple(self._deliveries):
            task.cancel()
        await asyncio.gather(*tuple(self._deliveries), return_exceptions=True)
