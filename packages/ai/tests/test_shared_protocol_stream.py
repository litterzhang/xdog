"""Protocol result wrappers keep terminal policy, laziness and owned cleanup."""
from __future__ import annotations

import asyncio

import pytest
from xdog.ai.core import AuthResult
from xdog.ai.protocols import anthropic_messages, openai_responses
from xdog.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    ErrorEvent,
    Model,
    StreamOptions,
    TextContent,
    TextDeltaEvent,
)


@pytest.fixture(params=[anthropic_messages, openai_responses], ids=['anthropic', 'responses'])
def protocol(request):
    return request.param


def open_stream(protocol):
    return protocol.stream(Model(id='test'), Context(), StreamOptions(), AuthResult(api_key='private-fake'))


@pytest.mark.parametrize('case', ['done', 'error', 'empty', 'delta-only', 'done-without-message',
                                  'error-without-message', 'terminal-not-last'])
@pytest.mark.parametrize('drive_result', [False, True])
async def test_terminal_result_policy_and_lazy_consumption(protocol, monkeypatch, case, drive_result):
    final = AssistantMessage(content=(TextContent(text='answer'),))
    error = AssistantMessage(stop_reason='error', error_message='provider error')
    events = {'done': [DoneEvent(message=final)], 'error': [ErrorEvent(error='provider error', message=error)],
              'empty': [], 'delta-only': [TextDeltaEvent(delta='partial')],
              'done-without-message': [DoneEvent()], 'error-without-message': [ErrorEvent(error='missing')],
              'terminal-not-last': [DoneEvent(message=final), TextDeltaEvent(delta='late')]}[case]
    trace = []
    async def source():
        trace.append('opened')
        try:
            for event in events:
                trace.append(event.type)
                yield event
        finally:
            trace.append('closed')
    def factory(*args):
        trace.append('created')
        return source()
    monkeypatch.setattr(protocol, '_stream_impl', factory)
    stream = open_stream(protocol)
    assert trace == []  # Building or closing an unused stream must not open a request.
    if drive_result:
        result = await stream.result()
    else:
        assert await stream.collect() == events
        result = await stream.result()
    if case in {'done', 'error'}:
        assert result is (final if case == 'done' else error)
    else:
        assert result.stop_reason == 'error'
        assert result.error_message == ('Empty stream' if case == 'empty' else 'Stream ended without done event')
    assert trace == ['created', 'opened', *[event.type for event in events], 'closed']
    before = list(trace)
    assert await stream.result() is result and trace == before
    await stream.aclose()
    assert trace == before


@pytest.mark.parametrize('after_delta', [False, True])
async def test_transport_failure_is_not_replaced_with_fabricated_terminal(protocol, monkeypatch, after_delta):
    failure = OSError('transport failure')
    closed = []
    async def source(*args):
        try:
            if after_delta:
                yield TextDeltaEvent(delta='partial')
            raise failure
        finally:
            closed.append('closed')
    monkeypatch.setattr(protocol, '_stream_impl', source)
    stream = open_stream(protocol)
    with pytest.raises(OSError) as raised:
        await stream.collect()
    assert raised.value is failure and closed == ['closed']
    # Existing API raises during iteration; it does not invent a successful final message.
    assert not stream._result_future.done()
    await stream.aclose()


async def test_cancel_during_active_iteration_closes_source_without_success_result(protocol, monkeypatch):
    entered, closed = asyncio.Event(), asyncio.Event()
    async def source(*args):
        try:
            entered.set()
            await asyncio.Event().wait()
            yield DoneEvent(message=AssistantMessage())
        finally:
            closed.set()
    monkeypatch.setattr(protocol, '_stream_impl', source)
    stream = open_stream(protocol)
    consuming = asyncio.create_task(stream.result())
    await asyncio.wait_for(entered.wait(), 3)
    consuming.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consuming
    assert closed.is_set() and not stream._result_future.done()
    await stream.aclose()


async def test_explicit_early_close_joins_owned_iterator_not_just_outer_generator(protocol, monkeypatch):
    closed = []
    async def source():
        try:
            yield TextDeltaEvent(delta='partial')
            yield DoneEvent(message=AssistantMessage())
        finally:
            closed.append('closed')
    owned = source()  # Keep a reference: correct resource cleanup cannot depend on garbage collection.
    monkeypatch.setattr(protocol, '_stream_impl', lambda *args: owned)
    stream = open_stream(protocol)
    iterator = stream.__aiter__()
    try:
        assert (await anext(iterator)).delta == 'partial'
        await iterator.aclose()  # API requires active iteration to stop before explicit stream close.
        await stream.aclose()
        assert closed == ['closed'], 'Explicit close returned before its actual source cleanup'
        assert not stream._result_future.done()
        await stream.aclose()
        assert closed == ['closed']
    finally:
        await owned.aclose()
        await iterator.aclose()
        await stream.aclose()


async def test_close_without_iteration_is_lazy(protocol, monkeypatch):
    def forbidden(*args):
        pytest.fail('Closing an unopened stream must not construct a protocol request')
    monkeypatch.setattr(protocol, '_stream_impl', forbidden)
    stream = open_stream(protocol)
    await stream.aclose()
    assert not stream._result_future.done()


async def test_owned_iterator_cleanup_never_closes_borrowed_client(protocol, monkeypatch):
    borrowed = type('BorrowedClient', (), {'closed': False})()
    class Events:
        def __init__(self):
            self.client = borrowed
            self.sent = False
            self.closed = 0
        def __aiter__(self):
            return self
        async def __anext__(self):
            if self.sent:
                raise StopAsyncIteration
            self.sent = True
            return DoneEvent(message=AssistantMessage())
        async def aclose(self):
            self.closed += 1
    events = Events()
    monkeypatch.setattr(protocol, '_stream_impl', lambda *args: events)
    stream = open_stream(protocol)
    await stream.result()
    await stream.aclose()
    assert events.closed == 1 and borrowed.closed is False


def test_shared_wrapper_stays_in_ai_without_agent_or_application_dependencies():
    import subprocess
    import sys
    code = '''
import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.startswith(('xdog.agent', 'xdog.coding', 'xdog.claw', 'xdog.tui')):
            raise AssertionError('AI wrapper acquired higher-level dependency: ' + name)
sys.meta_path.insert(0, Guard())
from xdog.ai.protocols._message_stream import message_stream
assert callable(message_stream)
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
