"""Shared terminal-message policy around protocol-owned event iterators.

Wire parsing and authentication stay in the protocol. The source factory remains
lazy, and cleanup closes only its iterator, never an unrelated/shared client.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from xdog.ai.types import AssistantMessage, AssistantMessageEvent, DoneEvent, ErrorEvent
from xdog.ai.utils.event_stream import EventStream


def message_stream(source: Callable[[], AsyncIterator[AssistantMessageEvent]]) -> EventStream[AssistantMessage]:
    result: asyncio.Future[AssistantMessage] = asyncio.get_event_loop().create_future()

    async def generate() -> AsyncIterator[AssistantMessageEvent]:
        iterator = source()
        last: AssistantMessageEvent | None = None
        try:
            async for event in iterator:
                last = event
                yield event
        finally:
            close = getattr(iterator, 'aclose', None)
            if callable(close):
                await close()  # Explicit consumer close must not rely on async-generator GC.
        if isinstance(last, (DoneEvent, ErrorEvent)) and last.message:
            result.set_result(last.message)
        else:
            result.set_result(AssistantMessage(
                content=(), stop_reason='error',
                error_message='Empty stream' if last is None else 'Stream ended without done event',
            ))

    return EventStream.from_async_generator(generate(), result)
