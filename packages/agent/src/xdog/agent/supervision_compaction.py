"""Child adapter plumbing for Agent's built-in compaction, never a summary policy."""
from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable

from xdog.agent.core import ModelStreamFn, StreamFn
from xdog.ai.types import AssistantMessage, AssistantMessageEvent, Context, Model, StreamOptions
from xdog.ai.utils.event_stream import EventStream


class _MetadataStream:
    def __init__(self, stream: StreamFn, metadata: ModelStreamFn) -> None:
        self.stream = stream
        self.metadata = metadata

    def resolve_model(self, name: str) -> Model | None:
        # Resolve on the executing adapter each time; never revive withdrawn
        # capabilities using a selection-time snapshot or a global catalog.
        return self.metadata.resolve_model(name)

    def __call__(self, model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
        return self.stream(model, context, options)


def preserve_model_metadata(stream: StreamFn, original: StreamFn) -> StreamFn:
    return _MetadataStream(stream, original) if isinstance(original, ModelStreamFn) else stream


class ArchivedSummaryStream(EventStream[AssistantMessage]):
    """Gate a native summary request on durable archival of its original history.

    No model/agent loop, summary rewriting or retry. Agent retains ownership of
    planning/commit/cancellation and closes this request through its native path.
    """

    def __init__(self, start: Callable[[], EventStream[AssistantMessage]],
                 archive: Callable[[], Awaitable[None]]) -> None:
        self.start = start
        self.archive = archive
        self.source: EventStream[AssistantMessage] | None = None
        self.closed = False

    async def _source(self) -> EventStream[AssistantMessage]:
        if self.closed:
            raise RuntimeError("Child summary stream already closed")
        if self.source is None:
            await self.archive()
            response = self.start()
            if inspect.isawaitable(response):
                response = await response
            self.source = response
        return self.source

    async def _events(self) -> AsyncIterator[AssistantMessageEvent]:
        source = await self._source()
        iterator = source.__aiter__()
        try:
            async for event in iterator:
                yield event
        finally:
            close = getattr(iterator, "aclose", None)
            if callable(close):
                await close()

    def __aiter__(self) -> AsyncIterator[AssistantMessageEvent]:
        return self._events()

    async def result(self) -> AssistantMessage:
        return await (await self._source()).result()

    async def aclose(self) -> None:
        if not self.closed:
            self.closed = True
            if self.source is not None:
                await self.source.aclose()
