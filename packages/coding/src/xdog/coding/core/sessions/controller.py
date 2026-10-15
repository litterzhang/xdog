"""Single session execution owner with thread-safe submission and correlated events."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from xdog.agent.session_input import SessionInput, decode_input, input_data
from xdog.coding.core.sessions.models import InputRecord, SessionEvent, Submission, TurnResult
from xdog.coding.core.sessions.store import InputStore

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession

logger = logging.getLogger(__name__)
T = TypeVar("T")
MAX_PENDING = 128


@dataclass
class _Job:
    value: SessionInput
    peer: str
    ticket: Submission
    branch: str
    generation: str
    finishing: bool = False


class SessionController:
    """Bind once to an async mode's loop, or own a loop thread for synchronous hosts."""

    def __init__(
        self,
        session: AgentSession,
        *,
        bound: bool,
        execute: Callable[[SessionInput], Awaitable[TurnResult]] | None = None,
    ) -> None:
        self.session = session
        self.bound = bound
        self.store = InputStore(session)
        self.generation = uuid.uuid4().hex
        self.branch = session.session_data.active_branch
        self._execute = execute or self._default_execute
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._start_lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._controls: deque[tuple[Callable[[], Awaitable[Any]], str, Future[Any]]] = deque()
        self._admissions: set[asyncio.Task[None]] = set()
        self._listeners: list[Callable[[SessionEvent], None]] = []
        self._active: asyncio.Task[None] | None = None
        self._current: _Job | None = None
        self._revoked: set[str] = set()
        self._budget = 0
        self._human_pending = False
        self._closing = False
        self._closed: Future[None] = Future()
        self._broken: BaseException | None = None

    def start(self) -> None:
        with self._start_lock:
            if self._loop is not None:
                return
            if self._closing:
                raise RuntimeError("Session controller is closed")
            try:
                self._loop = asyncio.get_running_loop()
                self._ready.set()
            except RuntimeError:
                self._thread = threading.Thread(target=self._thread_main, name="xdog-session-owner", daemon=True)
                self._thread.start()
                self._ready.wait()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self._settle(self._closed, error=exc)
        finally:
            self._settle(self._closed, None)

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._ready.set()
        await self._stop.wait()

    def _post(self, callback: Callable[[], None]) -> None:
        self.start()
        assert self._loop is not None
        self._loop.call_soon_threadsafe(callback)

    @staticmethod
    def _settle(future: Future[T], value: T | None = None, *, error: BaseException | None = None) -> None:
        if future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(value)  # type: ignore[arg-type]

    def subscribe(self, listener: Callable[[SessionEvent], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _emit(self, kind: str, job: _Job, data: object) -> None:
        event = SessionEvent(self.session.session_id, job.generation, job.value.id, kind, data)
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception:
                logger.exception("Session event consumer failed")

    async def _default_execute(self, value: SessionInput) -> TurnResult:
        from xdog.coding.core.sessions.turns import run_turn

        job = self._jobs[value.id]
        return await run_turn(self.session, value, emit=lambda event: self._emit("agent_event", job, event))

    def submit(self, value: SessionInput, *, peer_id: str) -> Submission:
        ticket = Submission(value.id, Future(), Future())
        try:
            if self._closing:
                raise RuntimeError("Session controller is closed")
            snapshot = decode_input(input_data(value))
            job = _Job(snapshot, peer_id, ticket, self.branch, self.generation)

            def spawn() -> None:
                task = asyncio.create_task(self._accept(job))
                self._admissions.add(task)
                task.add_done_callback(self._admissions.discard)

            self._post(spawn)
        except Exception as exc:
            self._settle(ticket.accepted, error=exc)
            self._settle(ticket.result, error=exc)
        return ticket

    async def _accept(self, job: _Job) -> None:
        try:
            if self._closing or job.peer in self._revoked:
                raise RuntimeError("Session connection is closed")
            if self._broken is not None:
                raise RuntimeError("Session persistence is unavailable") from self._broken
            if len(self._jobs) + len(self._admissions) > MAX_PENDING:
                raise ValueError("Session queue exceeds capacity")
            if job.value.id in self._jobs:
                raise ValueError("Duplicate request identity")
            paused = self.bound and job.value.origin != "human" and self._budget == 0
            record = InputRecord(job.value, job.branch, job.generation, "paused" if paused else "queued")
            await self.store.accept(record)
            self._jobs[job.value.id] = job
            self._emit("input_" + record.status, job, record)
            self._settle(job.ticket.accepted, None)
            if job.value.origin == "human":
                self._budget = 1
            if self._closing or job.peer in self._revoked:
                await self._finish(job, TurnResult("cancelled", None))
            elif job.generation != self.generation:
                await self._finish(job, TurnResult("discarded", None))
            self._pump()
        except BaseException as exc:
            self._settle(job.ticket.accepted, error=exc)
            self._settle(job.ticket.result, error=exc)

    def _pump(self) -> None:
        if self._closing or self._active is not None:
            return
        if self._controls:
            operation, name, future = self._controls.popleft()
            self._active = asyncio.create_task(self._run_control(operation, future))
            return
        jobs = [job for job in self._jobs.values() if not job.finishing]
        human = next((job for job in jobs if job.value.origin == "human"), None)
        if human is None and (self._human_pending or self.bound and self._budget <= 0):
            return
        selected = human or next(iter(jobs), None)
        if selected is not None:
            self._current = selected
            self._active = asyncio.create_task(self._run_job(selected))

    async def _run_control(self, operation: Callable[[], Awaitable[Any]], future: Future[Any]) -> None:
        try:
            self._settle(future, await operation())
        except BaseException as exc:
            self._settle(future, error=exc)
        finally:
            self._active = None
            self._pump()

    async def _run_job(self, job: _Job) -> None:
        try:
            if self._broken is not None:
                raise RuntimeError("Session persistence is unavailable") from self._broken
            if job.generation != self.generation:
                outcome = TurnResult("discarded", None)
            else:
                await self.store.transition(job.value.id, "running")
                self._emit("input_running", job, job.value)
                if self.bound and job.value.origin != "human":
                    self._budget -= 1
                outcome = await self._execute(job.value)
            await self._finish(job, outcome)
        except asyncio.CancelledError:
            if not self.session.agent.is_idle:
                await self.session.agent.cancel_and_wait()
            await self._finish(job, TurnResult("cancelled", None))
        except Exception as exc:
            await self._finish(job, TurnResult("failed", None, str(exc)))
        finally:
            self._current = None
            self._active = None
            self._pump()

    async def _finish(self, job: _Job, result: TurnResult) -> None:
        if job.finishing:
            return
        job.finishing = True
        try:
            await self.store.transition(job.value.id, result.status, result.error)
        except BaseException as exc:
            self._broken = exc
            result = TurnResult("failed", result.response, "Session persistence failed: " + str(exc))
        self._jobs.pop(job.value.id, None)
        self._emit("input_completed", job, result)
        self._settle(job.ticket.result, result)

    def control(self, operation: Callable[[], Awaitable[T]], *, name: str) -> Future[T]:
        future: Future[T] = Future()

        def enqueue() -> None:
            if self._closing:
                self._settle(future, error=RuntimeError("Session controller is closed"))
                return
            self._controls.append((operation, name, future))
            self._pump()

        self._post(enqueue)
        return future

    def human_input(self) -> None:
        def grant() -> None:
            self._budget = 1
            self._pump()

        self._post(grant)

    def set_human_pending(self, pending: bool) -> None:
        def change() -> None:
            self._human_pending = pending
            self._pump()

        self._post(change)

    def _async(self, operation: Callable[[], Awaitable[T]]) -> Future[T]:
        self.start()
        assert self._loop is not None

        async def run() -> T:
            return await operation()

        return asyncio.run_coroutine_threadsafe(run(), self._loop)

    def cancel(self, request_id: str, *, peer_id: str) -> Future[bool]:
        async def run() -> bool:
            job = self._jobs.get(request_id)
            if job is None or job.peer != peer_id:
                return False
            if self._current is job and self._active is not None:
                self._active.cancel()
            else:
                await self._finish(job, TurnResult("cancelled", None))
            return True

        return self._async(run)

    def disconnect(self, peer_id: str) -> Future[None]:
        async def run() -> None:
            self._revoked.add(peer_id)
            tasks = []
            for job in tuple(self._jobs.values()):
                if job.peer == peer_id:
                    if self._current is job and self._active is not None:
                        self._active.cancel()
                        tasks.append(self._active)
                    else:
                        await self._finish(job, TurnResult("cancelled", None))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        return self._async(run)

    def rotate(self, branch_id: str) -> None:
        async def run() -> None:
            self.branch, self.generation = branch_id, uuid.uuid4().hex
            self._budget = 0
            for job in tuple(self._jobs.values()):
                if job is not self._current:
                    await self._finish(job, TurnResult("discarded", None))

        self._async(run)

    def resume(self, peer_id: str) -> Future[bool]:
        async def run() -> bool:
            from xdog.agent import AgentContext, BeforeToolCallContext
            from xdog.ai.types import AssistantMessage, ToolCall

            captured = self.generation
            args = {"session_id": self.session.session_id, "generation": captured}
            context = BeforeToolCallContext(
                assistant_message=AssistantMessage(),
                tool_call=ToolCall(id=uuid.uuid4().hex, name="session_resume", arguments=args),
                args=args,
                context=AgentContext(),
            )
            verdict = await self.session.permissions.before_tool_call(context)
            allowed = verdict is None and not self._closing and captured == self.generation
            if allowed:
                self._budget = 1
                self._pump()
            return allowed

        return self._async(run)

    def close(self) -> Future[None]:
        if self._closing:
            return self._closed
        self.start()
        self._closing = True
        self._async(self._shutdown)
        return self._closed

    async def _shutdown(self) -> None:
        try:
            if self._admissions:
                await asyncio.gather(*tuple(self._admissions), return_exceptions=True)
            if self._active is not None:
                self._active.cancel()
                await asyncio.gather(self._active, return_exceptions=True)
            for job in tuple(self._jobs.values()):
                await self._finish(job, TurnResult("cancelled", None))
            while self._controls:
                _, _, future = self._controls.popleft()
                self._settle(future, error=RuntimeError("Session controller closed"))
        except BaseException as exc:
            self._settle(self._closed, error=exc)
        finally:
            if self._stop is not None:
                self._stop.set()
            else:
                self._settle(self._closed, None)
