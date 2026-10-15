"""Compose one live session's controller, writer lease and local identity."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any

from xdog.coding.core.sessions.controller import SessionController
from xdog.coding.core.sessions.ownership import OwnerRecord, OwnerRegistry, SessionLease, runtime_directory
from xdog.coding.core.sessions.router import SessionRouter

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession


class SessionRuntime:
    def __init__(self, session: AgentSession, *, bound: bool, router: SessionRouter | None = None) -> None:
        self.session = session
        claimed = session._lease is None
        if claimed:
            session._lease = SessionLease.acquire(runtime_directory(session.session_manager._dir), session.session_id)
        assert session._lease is not None
        try:
            self.registry = OwnerRegistry(session._lease.directory)
            self.controller = SessionController(session, bound=bound)
            self.router = router or SessionRouter()
            self.record = OwnerRecord(
                session.session_id,
                session.working_dir.resolve(),
                self.registry.socket_path(session.session_id),
                self.controller.generation,
            )
        except BaseException:
            if claimed:
                session._lease.close()
                session._lease = None
            raise
        self.controller._close_hook = self._aclose_resources
        self.producer_tasks: set[asyncio.Task[None]] = set()
        session.runtime = self
        self.server: Any = None
        self._endpoint_lock = asyncio.Lock()
        self._started = False
        self._closed: Future[None] | None = None
        self.disposing = False

    def start(self, *, own_thread: bool = False) -> None:
        if not self._started:
            self.controller.start(own_thread=own_thread)
            self.router.register(self.controller, self.record)
            self._started = True

    async def _aclose_resources(self) -> None:
        if self.producer_tasks:
            await asyncio.gather(*tuple(self.producer_tasks), return_exceptions=True)
        await self.session._aclose_resources()

    async def open_endpoint(self) -> None:
        async with self._endpoint_lock:
            if self.server is None:
                from xdog.coding.acp.server import serve_local
                self.server = await serve_local(self)

    def context_changed(self) -> None:
        old = self.record
        discarded = self.controller.rotate(self.session.session_data.active_branch)
        async def rebind() -> None:
            await asyncio.wrap_future(discarded)
            if self.server is not None:
                await self.server.close()
                self.server = None
            self.router.retire(old.session_id, old.generation)
            self.registry.remove(old)
            from xdog.coding.core.sessions.store import InputStore
            self.controller.store = InputStore(self.session)
            self.record = OwnerRecord(self.session.session_id, self.session.working_dir.resolve(),
                                      self.registry.socket_path(self.session.session_id), self.controller.generation)
            self.router.register(self.controller, self.record)
            await self.open_endpoint()
        self.controller.settle_before_release(rebind())

    def close(self) -> Future[None]:
        if self._closed is not None:
            return self._closed
        self._closed = Future()
        closed = self._closed

        def finalized(completed: Future[None]) -> None:
            try:
                completed.result()
                self.router.retire(self.record.session_id, self.record.generation)
                self.registry.remove(self.record)
                self.disposing = True
                self.session.dispose()
                if not closed.done():
                    closed.set_result(None)
            except BaseException as exc:
                if not closed.done():
                    closed.set_exception(exc)

        async def stop_endpoint() -> None:
            if self.server is not None:
                await self.server.close()
                self.server = None
        def stopped(completed: Future[None]) -> None:
            try:
                completed.result()
            except BaseException as exc:
                if not closed.done():
                    closed.set_exception(exc)
            self.controller.close().add_done_callback(finalized)
        self.controller._async(stop_endpoint).add_done_callback(stopped)
        return closed
