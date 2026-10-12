"""EventStream -- an async iterable that also exposes a final result.

This is the Python equivalent of the TypeScript ``EventStream<T>`` class.
It wraps an ``AsyncIterator`` of :class:`~ai.types.AssistantMessageEvent`
objects and resolves a final *result* value (typically an
:class:`~ai.types.AssistantMessage`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Generic, TypeVar

from xdog.ai.types import AssistantMessageEvent

T = TypeVar("T")


class EventStream(Generic[T]):
    """Async iterable of streaming events with a final result.

    Usage::

        stream = some_provider.stream(model, context, options)

        async for event in stream:
            handle(event)

        final_message = await stream.result()

    The class supports two construction patterns:

    1. **Pull-based** -- wrap an ``AsyncIterator`` (e.g. from an async generator).
    2. **Push-based** -- use :meth:`send` / :meth:`close` from a producer coroutine.

    Parameters
    ----------
    iterator:
        An async iterator yielding :class:`AssistantMessageEvent` items.
        Pass ``None`` to use push-based mode with :meth:`send`.
    result_future:
        An :class:`asyncio.Future` that will be resolved with the final
        result value once the stream is exhausted.
    """

    def __init__(
        self,
        iterator: AsyncIterator[AssistantMessageEvent] | None = None,
        result_future: asyncio.Future[T] | None = None,
    ) -> None:
        self._iterator = iterator
        self._queue: asyncio.Queue[AssistantMessageEvent | None] | None = (
            None if iterator is not None else asyncio.Queue()
        )
        if result_future is not None:
            self._result_future: asyncio.Future[T] = result_future
        else:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
            self._result_future = loop.create_future()

        self._consumed = False
        self._released = False
        self._listeners: list[object] = []

    # -- push-based producer API --------------------------------------------

    async def send(self, event: AssistantMessageEvent) -> None:
        """Push an event into the stream (push-based mode)."""
        if self._queue is None:
            raise RuntimeError("Cannot send() on a pull-based EventStream")
        if self._consumed:
            return
        await self._queue.put(event)
        for listener in self._listeners:
            if callable(listener):
                listener(event)

    def send_sync(self, event: AssistantMessageEvent) -> None:
        """Push an event synchronously (fire-and-forget)."""
        if self._queue is None:
            raise RuntimeError("Cannot send_sync() on a pull-based EventStream")
        if self._consumed:
            return
        self._queue.put_nowait(event)
        for listener in self._listeners:
            if callable(listener):
                listener(event)

    async def close(self) -> None:
        """Signal the end of a push-based stream."""
        if self._queue is None:
            return
        if self._consumed:
            return
        await self._queue.put(None)

    def close_sync(self) -> None:
        """Signal end of a push-based stream synchronously."""
        if self._queue is None:
            return
        if self._consumed:
            return
        self._queue.put_nowait(None)

    async def aclose(self) -> None:
        """Release the owned iterator; never close a shared provider client.

        Consumers must stop active iteration before calling this. Push-mode
        producers remain owned by their creator, who must settle those tasks.
        """
        if self._released:
            return
        close = getattr(self._iterator, "aclose", None)
        if callable(close):
            await close()
        if self._queue is not None:
            self._queue.put_nowait(None)
        self._consumed = True
        self._released = True

    # -- listener API -------------------------------------------------------

    def on(self, listener: object) -> object:
        """Register a synchronous listener. Returns an unsubscribe callable."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    # -- async iteration ----------------------------------------------------

    def __aiter__(self) -> AsyncIterator[AssistantMessageEvent]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[AssistantMessageEvent]:
        try:
            if self._iterator is not None:
                async for event in self._iterator:
                    yield event
            elif self._queue is not None:
                while True:
                    item = await self._queue.get()
                    if item is None:
                        break
                    yield item
        finally:
            self._consumed = True

    # -- result accessor ----------------------------------------------------

    async def result(self) -> T:
        """Await the final result produced after the stream ends.

        If the result is not ready and the stream has not been consumed,
        drive the iterator to obtain it. A ready result never advances the
        iterator (which may still own resources awaiting explicit closure).
        """
        if not self._result_future.done() and not self._consumed:
            async for _ in self:
                pass
        return await self._result_future

    def set_result(self, value: T) -> None:
        """Resolve the result future (called by the provider implementation)."""
        if not self._result_future.done():
            self._result_future.set_result(value)

    def set_exception(self, exc: BaseException) -> None:
        """Reject the result future with an exception."""
        if not self._result_future.done():
            self._result_future.set_exception(exc)

    @property
    def done(self) -> bool:
        """``True`` once the underlying iterator has been fully consumed."""
        return self._consumed

    # -- collect helper -----------------------------------------------------

    async def collect(self) -> list[AssistantMessageEvent]:
        """Consume the entire stream into a list."""
        items: list[AssistantMessageEvent] = []
        async for item in self:
            items.append(item)
        return items

    # -- factory helpers ----------------------------------------------------

    @classmethod
    def from_async_generator(
        cls,
        gen: AsyncIterator[AssistantMessageEvent],
        result_future: asyncio.Future[T] | None = None,
    ) -> EventStream[T]:
        """Create an ``EventStream`` from an async generator."""
        return cls(iterator=gen, result_future=result_future)

    @classmethod
    def empty(cls, result_value: T) -> EventStream[T]:
        """Return an already-exhausted stream whose result is *result_value*."""

        async def _empty_iter() -> AsyncIterator[AssistantMessageEvent]:
            return
            yield  # make it an async generator

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        fut: asyncio.Future[T] = loop.create_future()
        fut.set_result(result_value)
        return cls(iterator=_empty_iter(), result_future=fut)
