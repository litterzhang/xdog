"""Agent-scoped async event stream for ``AgentEvent`` items.

The ``pi_ai.utils.event_stream.EventStream`` class is strongly typed to
:class:`~pi_ai.types.AssistantMessageEvent` and includes a *result* future
pattern designed for LLM streaming.  The agent layer needs a simpler,
purely push-based stream typed to :data:`~agent.types.AgentEvent`.

This module provides :class:`AgentEventStream`, a lightweight async
iterable with push/close semantics that mirrors the ``EventStream`` API
but is generic over the event type.  It also supports a *result* future
so that callers can await a final value after the stream ends (matching
the TS ``EventStream<AgentEvent, AgentMessage[]>`` pattern).
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from typing import Any, Callable, Generic, TypeVar

E = TypeVar("E")


class AgentEventStream(Generic[E]):
    """Push-based async iterable of agent lifecycle events.

    Usage -- **producer** side::

        stream = AgentEventStream[AgentEvent]()
        await stream.send(AgentStartEvent())
        ...
        stream.end(result_value)

    Usage -- **consumer** side::

        async for event in stream:
            print(event.type)

        result = await stream.result()
    """

    def __init__(self, max_buffered_events: int = 0) -> None:
        if type(max_buffered_events) is not int or max_buffered_events < 0:
            raise ValueError("max_buffered_events must be a non-negative integer")
        self._capacity = max_buffered_events
        self._queue: deque[E] = deque()
        self._ready = asyncio.Event()
        self._space = asyncio.Event()
        self._space.set()
        self._final_event: E | None = None
        self._cleanup_events: deque[E] = deque()
        self._closed = False
        self._listeners: list[Callable[[E], Any]] = []
        # Result support (matches ai.utils.event_stream.EventStream)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        self._result_future: asyncio.Future[Any] = loop.create_future()

    # -- producer API -------------------------------------------------------

    async def send(self, event: E) -> None:
        """Send with bounded backpressure; cancellation interrupts the wait."""
        while not self._closed and self._capacity and len(self._queue) >= self._capacity:
            self._space.clear()
            await self._space.wait()
        if not self._closed:
            self.send_sync(event)

    def send_sync(self, event: E) -> None:
        """Send without waiting; bounded streams raise QueueFull rather than drop events."""
        if self._closed:
            return
        if self._capacity and len(self._queue) >= self._capacity:
            raise asyncio.QueueFull
        self._queue.append(event)
        self._ready.set()
        self._notify(event)

    def _notify(self, event: E) -> None:
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass  # Observation callbacks are not persistence barriers.

    def send_cleanup(self, event: E) -> None:
        """Reserve at most three shutdown observations without blocking cleanup.

        The owner uses these only for an interrupted compaction's known usage
        and completed/failed event. Ordinary traffic must use send().
        """
        if self._closed:
            return
        if len(self._cleanup_events) >= 3:
            raise asyncio.QueueFull
        self._cleanup_events.append(event)
        self._notify(event)

    def send_final(self, event: E) -> None:
        """Reserve one terminal event outside capacity, released after close.

        This allows cancellation/cleanup to finish even if nobody is consuming.
        It does not discard any already-buffered event.
        """
        if self._closed or self._final_event is not None:
            return
        self._final_event = event
        self._notify(event)

    def end(self, result: Any = None) -> None:
        """Close and resolve the existing result future without requiring a consumer."""
        if self._closed:
            return
        self._closed = True
        if not self._result_future.done():
            self._result_future.set_result(result)
        self._ready.set()
        self._space.set()

    async def close(self) -> None:
        self.end()

    def close_sync(self) -> None:
        self.end()

    # -- consumer API -------------------------------------------------------

    def on(self, listener: Callable[[E], Any]) -> Callable[[], None]:
        """Register a synchronous listener.  Returns an unsubscribe callable."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    async def collect(self) -> list[E]:
        """Consume the entire stream into a list."""
        items: list[E] = []
        async for item in self:
            items.append(item)
        return items

    async def result(self) -> Any:
        """Wait for completion without consuming events or cancelling the shared future.

        Bounded streams require a concurrent event consumer while the producer
        is active. Waiting for a result never silently discards those events.
        """
        return await asyncio.shield(self._result_future)

    # -- async iterator protocol --------------------------------------------

    def __aiter__(self) -> AsyncIterator[E]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[E]:
        while True:
            if self._queue:
                item = self._queue.popleft()
                self._space.set()
                yield item
            elif self._closed:
                while self._cleanup_events:
                    yield self._cleanup_events.popleft()
                if self._final_event is not None:
                    final, self._final_event = self._final_event, None
                    yield final
                return
            else:
                self._ready.clear()
                await self._ready.wait()

    @property
    def buffered_events(self) -> int:
        """Queued ordinary events (the single reserved terminal event is separate)."""
        return len(self._queue)

    @property
    def closed(self) -> bool:
        """Whether :meth:`close` or :meth:`end` has been called."""
        return self._closed
