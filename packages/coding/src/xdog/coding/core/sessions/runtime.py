"""Compose one live session's controller, writer lease and local identity."""

from __future__ import annotations

import asyncio
import sys
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any

from xdog.coding.core.sessions.controller import SessionController
from xdog.coding.core.sessions.inbox_runtime import InboxCoordinator
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.coding.core.sessions.ownership import OwnerRecord, OwnerRegistry, SessionLease, runtime_directory
from xdog.coding.core.sessions.router import SessionRouter

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession


class SessionRuntime:
    def __init__(self, session: AgentSession, *, bound: bool, router: SessionRouter | None = None,
                 inbox_store: InboxStore | None = None) -> None:
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
        self._retiring_leases: set[SessionLease] = set()
        self.controller.on_control_settled = self._release_retired_leases
        self.controller._close_hook = self._aclose_resources
        self.producer_tasks: set[asyncio.Task[None]] = set()
        session.runtime = self
        self.server: Any = None
        # A custom SessionManager must not write its inbox into ambient user data.
        store = inbox_store or InboxStore(session.session_manager._dir, session.session_manager._dir.parent / 'inbox')
        self.inbox = InboxCoordinator(self, store)
        self.controller.inbox = self.inbox
        self._endpoint_lock = asyncio.Lock()
        self._started = False
        self._closed: Future[None] | None = None
        self.disposing = False
        self._inbox_watch: asyncio.Task[None] | None = None

    def retain_lease(self, lease: SessionLease) -> None:
        """A navigated-away writer stays exclusive until all control cleanup settles."""
        self._retiring_leases.add(lease)

    def _release_retired_leases(self) -> None:
        for lease in tuple(self._retiring_leases):
            lease.close()
            self._retiring_leases.discard(lease)

    def start(self, *, own_thread: bool = False) -> None:
        if not self._started:
            self.controller.start(own_thread=own_thread)
            self.router.register(self.controller, self.record)
            self._started = True

    async def _aclose_resources(self) -> None:
        try:
            await self.inbox.retire()
        finally:
            try:
                if self.producer_tasks:
                    await asyncio.gather(*tuple(self.producer_tasks), return_exceptions=True)
            finally:
                await self.session._aclose_resources()

    async def start_inbox_watch(self) -> None:
        if self._closed is None and (self._inbox_watch is None or self._inbox_watch.done()):
            self._inbox_watch = asyncio.create_task(self._watch_inbox())

    async def _watch_inbox(self) -> None:
        error = ''
        while self._closed is None and not self.controller._closing:
            await asyncio.sleep(0.5)
            if self._closed is not None or self.controller._closing:
                break
            try:
                await self.inbox.snapshot()
                error = ''
            except (OSError, ValueError, RuntimeError) as exc:
                if self.record.generation != self.controller.generation:
                    continue  # A context rebind is still settling, not a new target to follow.
                if str(exc) != error:
                    error = str(exc)
                    self.controller.publish('inbox_error', error)

    async def _stop_inbox_watch(self) -> None:
        task, self._inbox_watch = self._inbox_watch, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def open_endpoint(self) -> None:
        if sys.version_info >= (3, 15):
            return
        async with self._endpoint_lock:
            if self.server is None:
                from xdog.coding.acp.server import serve_local
                self.server = await serve_local(self)

    def context_changed(self) -> None:
        old = self.record
        previous_inbox = self.inbox
        discarded = self.controller.rotate(self.session.session_data.active_branch)
        async def rebind() -> None:
            try:
                await asyncio.wrap_future(discarded)
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
                self.inbox = InboxCoordinator(self, previous_inbox.store)
                self.controller.inbox = self.inbox
                self.router.register(self.controller, self.record)
                await self.open_endpoint()
            except BaseException as exc:
                self.controller._broken = exc
                raise
        self.controller.settle_before_release(rebind())

    def close(self) -> Future[None]:
        if self._closed is not None:
            return self._closed
        self._closed = Future()
        closed = self._closed

        shutdown_error: BaseException | None = None

        def finalized(completed: Future[None]) -> None:
            error = shutdown_error
            try:
                completed.result()
            except BaseException as exc:
                error = error or exc
            try:
                self.router.retire(self.record.session_id, self.record.generation)
                self.registry.remove(self.record)
            except BaseException as exc:
                error = error or exc
            finally:
                self.disposing = True
                try:
                    self._release_retired_leases()
                    self.session.dispose()
                except BaseException as exc:
                    error = error or exc
            if not closed.done():
                if error is None:
                    closed.set_result(None)
                else:
                    closed.set_exception(error)

        async def stop_endpoint() -> None:
            await self._stop_inbox_watch()
            if self.server is not None:
                await self.server.close()
                self.server = None

        def stopped(completed: Future[None]) -> None:
            nonlocal shutdown_error
            try:
                completed.result()
            except BaseException as exc:
                shutdown_error = exc
            self.controller.close().add_done_callback(finalized)
        self.controller._async(stop_endpoint).add_done_callback(stopped)
        return closed
