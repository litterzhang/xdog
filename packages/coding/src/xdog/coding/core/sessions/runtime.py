"""Compose one live session's controller, writer lease and local identity."""

from __future__ import annotations

from concurrent.futures import Future
from typing import TYPE_CHECKING

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
        self._started = False
        self._closed: Future[None] | None = None
        self.disposing = False

    def start(self) -> None:
        if not self._started:
            self.controller.start()
            self.router.register(self.controller, self.record)
            self._started = True

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
                closed.set_result(None)
            except BaseException as exc:
                closed.set_exception(exc)

        self.controller.close().add_done_callback(finalized)
        return self._closed
