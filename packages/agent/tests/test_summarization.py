"""Tool-free selected-adapter summarization never commits an incomplete substitute."""
import asyncio

import pytest
from xdog.agent.summarization import stream_summary, summary_instructions
from xdog.ai.types import AssistantMessage, Context, StreamOptions, TextContent, ToolCall, UserMessage
from xdog.ai.utils.event_stream import EventStream


@pytest.mark.parametrize('message', [AssistantMessage(),
    AssistantMessage(content=(TextContent(text='partial'),), stop_reason='length'),
    AssistantMessage(content=(TextContent(text='partial'),), stop_reason='error', error_message='secret'),
    AssistantMessage(content=(TextContent(text='partial'),), stop_reason='aborted'),
    AssistantMessage(content=(ToolCall(id='no', name='bash', arguments={'command': 'never execute'}),)),
    AssistantMessage(content=(TextContent(text='x' * (256 * 1024 + 1)),))])
async def test_incomplete_empty_or_tool_summary_is_rejected(message):
    with pytest.raises(ValueError, match='original history retained') as failure:
        await stream_summary(lambda *args: EventStream.empty(message), 'selected/model', Context())
    assert 'secret' not in str(failure.value)


async def test_async_adapter_current_model_options_no_tools_no_fallback():
    calls = []
    async def adapter(model, context, options):
        calls.append((model, context, options))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='valid summary'),)))
    context = Context(messages=(UserMessage(content='old conversation'),), tools=())
    options = StreamOptions(thinking='low', web_search=True, max_tokens=64)
    assert await stream_summary(adapter, 'selected/model', context, options) == 'valid summary'
    assert calls[0][0] == 'selected/model' and calls[0][1] is context
    assert calls[0][2].thinking == 'low' and calls[0][2].max_tokens == 64
    assert calls[0][2].web_search is False and calls[0][2].cancel.is_set()
    assert options.web_search is True and options.cancel is None


async def test_provider_exception_is_not_private_ui_output():
    def fail(*args):
        raise OSError('PRIVATE-CREDENTIAL-AND-CONTEXT')
    with pytest.raises(ValueError) as failure:
        await stream_summary(fail, 'selected/model', Context())
    assert 'PRIVATE' not in str(failure.value)


@pytest.mark.parametrize('value', ['\x00', 'x' * 16385, None, [], {}])
def test_invalid_focus_rejected(value):
    with pytest.raises(ValueError):
        summary_instructions(value)


async def test_cancel_closes_pull_generator_and_owned_options():
    entered, closed = asyncio.Event(), asyncio.Event()
    options_seen = []
    def adapter(model, context, options):
        options_seen.append(options)
        async def wait():
            try:
                entered.set()
                await asyncio.Event().wait()
                yield None
            finally:
                closed.set()
        return EventStream.from_async_generator(wait())
    task = asyncio.create_task(stream_summary(adapter, 'selected/model', Context()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and options_seen[0].cancel.is_set()
