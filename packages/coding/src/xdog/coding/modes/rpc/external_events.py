"""Idle RPC dispatch with one retained stdin read and normal session turns."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.external_events.binding import ExternalEventBinding, session_busy
from xdog.coding.core.external_events.messages import serialize
from xdog.coding.core.external_events.protocol import JsonValue
from xdog.coding.modes.rpc.input import RpcInput


class RpcExternalEvents:
    def __init__(self, session: AgentSession, emit: Callable[[dict[str, Any]], None], *,
                 busy: Callable[[], bool], run: Callable[[str], Awaitable[bool]]) -> None:
        self.session, self.emit, self.busy, self.run = session, emit, busy, run
        self.loop = asyncio.get_running_loop()
        self.binding: ExternalEventBinding | None = None
        self.wake = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self._reading: asyncio.Task[tuple[str | None, str]] | None = None
        self._waking: asyncio.Task[bool] | None = None

    def notify(self) -> None:
        self.loop.call_soon_threadsafe(self.wake.set)

    async def start(self) -> None:
        if isinstance(self.session, AgentSession):
            self.binding = await asyncio.to_thread(self.session.enable_external_events, notify=self.notify)

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def human_input(self) -> None:
        if self.binding is not None:
            self.binding.inbox.human_input()

    def publish(self) -> None:
        if self.binding is not None:
            state = self.binding.inbox.snapshot()
            self.emit({"type": "external_event_status", "pending": state["pending_count"],
                       "registrations": state["registration_count"], "wake_budget": state["wake_budget"],
                       "paused_reason": state["paused_reason"],
                       "processing": cast(list[JsonValue], state["processing"])[:8]})

    async def _run(self, serialized: str, identifier: str) -> None:
        assert self.binding is not None
        try:
            success = await self.run(serialized)
            self.binding.inbox.complete(identifier, status="succeeded" if success else "failed")
        except asyncio.CancelledError:
            self.binding.inbox.complete(identifier, status="cancelled")
            raise
        except Exception as exc:
            self.binding.inbox.complete(identifier, status="failed", error=str(exc))
            self.emit({"type": "error", "action": "external_event", "message": str(exc)})
        finally:
            self.publish()
            self.wake.set()

    def poll(self) -> None:
        if self.binding is None or self.running:
            return
        if self.task is not None:
            if not self.task.cancelled():
                self.task.exception()
            self.task = None
        job = self.binding.inbox.take(busy=self.busy() or session_busy(self.session), human_pending=False)
        if job is not None:
            try:
                serialized = serialize(job)
            except Exception as exc:
                self.binding.inbox.complete(job.id, status="failed", error=str(exc))
                self.publish()
                return
            self.task = asyncio.create_task(self._run(serialized, job.id))
            self.publish()

    async def read(self, inputs: RpcInput, failure: asyncio.Future[None]) -> tuple[str | None, str]:
        if self._reading is None:
            self._reading = asyncio.create_task(inputs.read())
        while True:
            if self._waking is None:
                self._waking = asyncio.create_task(self.wake.wait())
            await asyncio.wait((self._reading, self._waking, failure), timeout=0.05,
                               return_when=asyncio.FIRST_COMPLETED)
            if failure.done():
                raise EOFError
            if self._waking.done():
                self.wake.clear()
                self._waking = None
                self.publish()
            # Give ready stdin completion priority over an automatic dispatch.
            await asyncio.sleep(0)
            if self._reading.done():
                reading, self._reading = self._reading, None
                return reading.result()
            self.poll()

    def abort(self) -> None:
        if self.running and self.task is not None:
            self.task.cancel()

    async def aclose(self) -> None:
        self.abort()
        tasks = [task for task in (self.task, self._reading, self._waking) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.binding is not None:
            await asyncio.to_thread(self.binding.close)
