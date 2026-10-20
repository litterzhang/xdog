"""Compose one live session's controller, writer lease and local identity."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any

from xdog.coding.core.sessions.availability import acp_available
from xdog.coding.core.sessions.controller import SessionController
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.coding.core.sessions.intake import attach_inbox
from xdog.coding.core.sessions.ownership import (
    OwnerRecord,
    OwnerRegistry,
    SessionLease,
    lease_directory,
    runtime_directory,
)
from xdog.coding.core.sessions.router import SessionRouter

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession


class SessionRuntime:
    def __init__(self, session: AgentSession, *, bound: bool, router: SessionRouter | None = None,
                 inbox_store: InboxStore | None = None) -> None:
        self.session = session
        claimed = session._lease is None
        if claimed:
            session._lease = SessionLease.acquire(lease_directory(session.session_manager._dir), session.session_id)
        assert session._lease is not None
        try:
            self.registry = OwnerRegistry(runtime_directory(session.session_manager._dir))
            self.controller = SessionController(session, bound=bound)
            self.router = router or SessionRouter()
            self.record = OwnerRecord(
                session.session_id,
                session.working_dir.resolve(),
                self.registry.socket_path(session.session_id),
                self.controller.generation,
            )
            self.controller.on_control_settled = session._release_retired_leases
            self.controller._close_hook = self._aclose_resources
            self.server: Any = None
            # Custom session stores never write inbox state into ambient user data.
            store = inbox_store or InboxStore(
                session.session_manager._dir, session.session_manager._dir.parent / 'inbox')
            self.inbox = attach_inbox(self.controller, store)
            self._endpoint_lock = asyncio.Lock()
            self._started = False
            self._closed: Future[None] | None = None
            self.disposing = False
        except BaseException:
            if claimed:
                session._lease.close()
                session._lease = None
            raise
        session.runtime = self  # Publish only a fully constructed owner.

    def start(self, *, own_thread: bool = False, setup_pending: bool = False) -> None:
        if not self._started:
            self.controller.start(own_thread=own_thread)
            self.router.register(self.controller, self.record)
            self._started = True
            if not setup_pending:
                self.controller._async(self.activate_inputs)

    async def _aclose_resources(self) -> None:
        from xdog.coding.core.session_lifecycle import close_input_owner
        await close_input_owner(self.session, self.controller, resources=True)

    async def activate_inputs(self) -> None:
        await self.inbox.activate()

    async def start_inbox_watch(self) -> None:
        await self.activate_inputs()

    async def _stop_inbox_watch(self) -> None:
        await self.inbox.stop_polling()

    async def open_endpoint(self) -> None:
        if not acp_available():
            return
        async with self._endpoint_lock:
            if self.server is None:
                try:
                    from xdog.coding.acp.server import serve_local
                except ImportError:
                    return  # Ordinary session ownership/inbox works without optional ACP.
                self.server = await serve_local(self)

    def context_changed(self) -> None:
        old = self.record
        previous_inbox = self.inbox
        discarded = self.controller.rotate(self.session.session_data.active_branch)
        async def rebind() -> None:
            try:
                await asyncio.wrap_future(discarded)
                await self.session._settle_retired_goals()
                await self.session._settle_retired_resources()
                if self.server is not None:
                    await self.server.close()
                    self.server = None
                await previous_inbox.retire()
                self.router.retire(old.session_id, old.generation)
                self.registry.remove(old)
                from xdog.coding.core.sessions.store import InputStore
                self.controller.store = InputStore(self.session)
                self.record = OwnerRecord(
                    self.session.session_id, self.session.working_dir.resolve(),
                    self.registry.socket_path(self.session.session_id), self.controller.generation,
                )
                self.inbox = attach_inbox(self.controller, previous_inbox.store)
                await self.inbox.activate()
                self.router.register(self.controller, self.record)
                await self.open_endpoint()
            except BaseException as exc:
                self.controller._broken = exc
                raise
        self.controller.settle_before_release(rebind())

    def close(self) -> Future[None]:
        from xdog.coding.core.session_lifecycle import request_close
        return request_close(self.session)

    def _close_owner(self) -> Future[None]:
        if self._closed is not None:
            return self._closed
        self._closed = Future()
        closed = self._closed

        shutdown_errors: list[BaseException] = []

        def finalized(completed: Future[None]) -> None:
            from xdog.coding.core.session_lifecycle import settle_sync
            def result() -> None:
                completed.result()
            try:
                settle_sync(result,
                    lambda: self.router.retire(self.record.session_id, self.record.generation),
                    lambda: self.registry.remove(self.record), message='Coding endpoint retirement failed')
            except BaseException as exc:
                shutdown_errors.append(exc)
            self.disposing = True
            if len(shutdown_errors) == 1:
                closed.set_exception(shutdown_errors[0])
            elif shutdown_errors:
                closed.set_exception(BaseExceptionGroup('Coding runtime close failed', shutdown_errors))
            else:
                closed.set_result(None)

        async def stop_endpoint() -> None:
            from xdog.agent.async_work import settle_cleanup
            async def server() -> None:
                if self.server is not None:
                    await self.server.close()
                    self.server = None
            await settle_cleanup(self._stop_inbox_watch, server, message='Coding ingress close failed')

        def stopped(completed: Future[None]) -> None:
            try:
                completed.result()
            except BaseException as exc:
                shutdown_errors.append(exc)
            self.controller.close().add_done_callback(finalized)
        self.controller._async(stop_endpoint).add_done_callback(stopped)
        return closed
