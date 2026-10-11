"""Single tool-free summary request on the caller's actual model adapter."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace

from xdog.agent.core import StreamFn
from xdog.ai.types import Context, StreamOptions, TextContent, ToolCall


def summary_instructions(value: str) -> str:
    if not isinstance(value, str) or len(value.encode('utf-8')) > 16384 or '\x00' in value:
        raise ValueError('Compaction instructions must be text, at most 16 KiB')
    return value.strip()


async def stream_summary(stream_fn: StreamFn, model: str, context: Context,
                         options: StreamOptions = StreamOptions()) -> str:
    """No primary-provider fallback, tool execution, or partial/error summary commits."""
    if not model or context.tools:
        raise ValueError('Summarization requires a selected model and no tools')
    cancel = asyncio.Event()
    response = None
    iterator = None
    try:
        async with asyncio.timeout(120):
            response = stream_fn(model, context, replace(options, cancel=cancel, web_search=False,
                                                         tool_choice=None, response_format=None))
            if inspect.isawaitable(response):
                response = await response
            iterator = response.__aiter__()
            async for _ in iterator:
                pass
            result = await response.result()
            if (result.stop_reason in {'error', 'aborted', 'length'} or result.error_message
                    or any(isinstance(part, ToolCall) for part in result.content)):
                raise ValueError('Compaction summary failed or was incomplete; original history retained')
            text = '\n\n'.join(part.text for part in result.content if isinstance(part, TextContent)).strip()
            if not text or len(text.encode('utf-8')) > 256 * 1024:
                raise ValueError('Compaction summary empty or oversized; original history retained')
            return text
    except Exception as exc:
        raise ValueError('Compaction summary request failed; original history retained') from exc
    finally:
        cancel.set()
        close = getattr(iterator, 'aclose', None)
        if callable(close):
            try:
                await close()
            except Exception as exc:
                raise ValueError('Compaction stream cleanup failed; original history retained') from exc
