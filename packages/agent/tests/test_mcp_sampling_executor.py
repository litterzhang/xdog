"""Configured adapter sampling calls are isolated, reviewed, bounded and never execute tools."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent.mcp.sampling_executor import SamplingExecutor
from xdog.agent.mcp.sampling_owner import SamplingOwner
from xdog.agent.mcp.stdio import McpError
from xdog.ai.types import AssistantMessage, DoneEvent, Model, TextContent, ThinkingContent, ToolCall
from xdog.ai.utils.event_stream import EventStream

from packages.agent.tests.test_mcp_sampling_owner import answer, request, selection, until


class Adapter:
    def __init__(self, message=None):
        self.calls = []
        self.message = message or AssistantMessage(content=(TextContent(text='MODEL OUTPUT'),))

    def resolve_model(self, name):
        return Model(id=name, context_window=32000, input=('text', 'image'))

    def __call__(self, model, context, options):
        self.calls.append((model, context, options))
        message = self.message
        async def events():
            yield DoneEvent(message=message)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)


async def test_same_configured_adapter_two_phase_execution_preserves_request_options_and_no_parent_context():
    adapter, events = Adapter(), []
    source = request()
    source['params'].update(systemPrompt='EXPLICIT SERVER SYSTEM', temperature=.3, stopSequences=['STOP'])
    execute = SamplingExecutor(adapter, ('explicit-model',), lambda: True)
    owner = SamplingOwner('view', events.append, execute, lambda: True)
    work = asyncio.create_task(owner.handle(source))
    try:
        await until(lambda: events)
        assert not adapter.calls
        answer(owner, events[0], selection=selection())
        await until(lambda: len(events) == 2)
        assert len(adapter.calls) == 1 and not work.done()
        model, context, options = adapter.calls[0]
        assert model == 'explicit-model' and context.system_prompt == 'EXPLICIT SERVER SYSTEM'
        assert len(context.messages) == 1 and 'UNTRUSTED REQUEST' in str(context.messages)
        assert context.tools is None and options.max_tokens == 10 and options.temperature == .3
        assert options.stop_sequences == ('STOP',) and not options.web_search
        answer(owner, events[1])
        assert (await work)['content']['text'] == 'MODEL OUTPUT'
    finally:
        await owner.close()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize('fault', ['model', 'context', 'token', 'audio', 'unsettled-call', 'required-tools'])
async def test_unsupported_or_unapproved_selection_refused_before_adapter_call(fault):
    adapter = Adapter()
    execute = SamplingExecutor(adapter, ('explicit-model',), lambda: True)
    source, chosen = request(), selection()
    if fault == 'model':
        chosen['model'] = 'not-in-host-catalog'
    elif fault == 'context':
        source['params']['includeContext'] = 'allServers'
        chosen['context'] = 'thisServer'
    elif fault == 'token':
        chosen['max_tokens'] = 21
    elif fault == 'audio':
        source['params']['messages'][0]['content'] = {'type': 'audio', 'data': 'AAEC', 'mimeType': 'audio/wav'}
    elif fault == 'unsettled-call':
        source['params']['messages'] = [{'role': 'assistant', 'content': {
            'type': 'tool_use', 'name': 'proposed', 'id': 'call', 'input': {}}}]
    else:
        source['params'].update(tools=[{'name': 'proposed', 'inputSchema': {'type': 'object'}}],
                                toolChoice={'mode': 'required'})
    with pytest.raises(McpError):
        await execute(source, chosen, asyncio.Event())
    assert not adapter.calls


async def test_proposed_tool_definition_only_no_execution_and_private_reasoning_signature_withheld():
    adapter = Adapter(AssistantMessage(content=(ThinkingContent(thinking='PRIVATE REASONING',
        thinking_signature='PRIVATE SIGNATURE'), ToolCall(id='proposal', name='approved', arguments={'n': 1})),
        stop_reason='toolUse'))
    execute = SamplingExecutor(adapter, ('explicit-model',), lambda: True)
    source = request()
    source['params'].update(tools=[{'name': 'approved', 'inputSchema': {'type': 'object'}}],
                            toolChoice={'mode': 'required'})
    chosen = {**selection(), 'tools': ['approved']}
    output = await execute(source, chosen, asyncio.Event())
    assert output['content'] == [{'type': 'tool_use', 'id': 'proposal', 'name': 'approved', 'input': {'n': 1}}]
    assert 'PRIVATE' not in str(output)
    assert len(adapter.calls) == 1 and adapter.calls[0][1].tools[0].name == 'approved'
    assert adapter.calls[0][2].tool_choice.type == 'any'


async def test_explicit_context_resolver_is_only_called_for_approved_scope():
    from xdog.ai.types import UserMessage
    adapter, scopes = Adapter(), []
    async def resolve(scope):
        scopes.append(scope)
        return (UserMessage(content='EXPLICIT HOST SNAPSHOT'),)
    execute = SamplingExecutor(adapter, ('explicit-model',), lambda: True, context=resolve)
    source = request()
    source['params']['includeContext'] = 'allServers'
    await execute(source, selection(), asyncio.Event())
    assert not scopes and len(adapter.calls[0][1].messages) == 1
    await execute(source, {**selection(), 'context': 'thisServer'}, asyncio.Event())
    assert scopes == ['thisServer'] and len(adapter.calls[1][1].messages) == 2


@pytest.mark.parametrize('reason', ['error', 'aborted'])
async def test_provider_failures_withhold_partial_output(reason):
    adapter = Adapter(AssistantMessage(content=(TextContent(text='PRIVATE PARTIAL'),), stop_reason=reason))
    execute = SamplingExecutor(adapter, ('explicit-model',), lambda: True)
    with pytest.raises(McpError, match='failed/canceled'):
        await execute(request(), selection(), asyncio.Event())


async def test_cancel_and_timeout_close_actual_provider_async_iterator():
    entered, closed = asyncio.Event(), asyncio.Event()
    def stream(model, context, options):
        async def events():
            try:
                entered.set()
                await asyncio.sleep(30)
            finally:
                closed.set()
            yield DoneEvent(message=AssistantMessage())
        return EventStream.from_async_generator(events())
    execute = SamplingExecutor(stream, ('explicit-model',), lambda: True, timeout=.1)
    with pytest.raises(TimeoutError):
        await execute(request(), selection(), asyncio.Event())
    assert closed.is_set()
    closed.clear()
    execute.timeout = 5
    task = asyncio.create_task(execute(request(), selection(), asyncio.Event()))
    entered.clear()
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_direct_cancel_event_interrupts_stalled_provider_without_waiting_for_timeout():
    entered, closed = asyncio.Event(), asyncio.Event()
    def stream(model, context, options):
        async def events():
            try:
                entered.set()
                await asyncio.sleep(30)
            finally:
                closed.set()
            yield DoneEvent(message=AssistantMessage())
        return EventStream.from_async_generator(events())
    execute = SamplingExecutor(stream, ('explicit-model',), lambda: True, timeout=10)
    cancel = asyncio.Event()
    task = asyncio.create_task(execute(request(), selection(), cancel))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        cancel.set()
        with pytest.raises(McpError, match='owner revoked'):
            await asyncio.wait_for(task, 2)
        assert closed.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_actual_adapter_output_budget_refuses_before_provider_and_resolved_context_is_bounded():
    adapter = Adapter()
    adapter.resolve_model = lambda name: Model(id=name, max_tokens=5)
    execute = SamplingExecutor(adapter, ('explicit-model',), lambda: True)
    with pytest.raises(McpError, match='model limits'):
        await execute(request(), selection(), asyncio.Event())
    assert not adapter.calls
    from xdog.ai.types import UserMessage
    async def context(scope):
        return (UserMessage(content='x' * 300000),)
    adapter.resolve_model = lambda name: Model(id=name)
    execute.context = context
    source = request()
    source['params']['includeContext'] = 'thisServer'
    with pytest.raises(McpError, match='retention limit'):
        await execute(source, {**selection(), 'context': 'thisServer'}, asyncio.Event())
    assert not adapter.calls


class CatalogAdapter(Adapter):
    def __init__(self):
        super().__init__()
        self.catalog = (Model(id='explicit-model', context_window=32000),
                        Model(id='alternate-model', context_window=64000))

    def models(self):
        return self.catalog

    def resolve_model(self, name):
        return next((row for row in self.catalog if row.id == name), None)


def test_catalog_pins_only_same_adapter_public_ids_and_withdrawal_refuses():
    from dataclasses import replace

    from xdog.agent.mcp.sampling_executor import catalog_current, model_catalog
    adapter = CatalogAdapter()
    names, profiles = model_catalog(adapter, 'explicit-model')
    assert names == ('explicit-model', 'alternate-model')
    assert catalog_current(adapter, 'explicit-model', names, profiles)
    adapter.catalog = (adapter.catalog[0], replace(adapter.catalog[1], max_tokens=3))
    assert not catalog_current(adapter, 'explicit-model', names, profiles)
    adapter.catalog = ()
    assert not catalog_current(adapter, 'explicit-model', names, profiles)
    with pytest.raises(McpError, match='unavailable'):
        model_catalog(adapter, 'explicit-model')


def test_wrapped_opaque_adapter_keeps_explicit_model_but_real_catalog_withdrawal_refuses():
    from types import SimpleNamespace

    from xdog.agent.extensions.providers import ExtensionProviderStream
    from xdog.agent.mcp.sampling_executor import model_catalog
    runtime = SimpleNamespace(provider_models=lambda: ())
    wrapped = ExtensionProviderStream(runtime, lambda *args: EventStream.empty(AssistantMessage()))
    assert model_catalog(wrapped, 'explicit-model') == (('explicit-model',), ())
    metadata = ExtensionProviderStream(runtime, Adapter())
    assert model_catalog(metadata, 'explicit-model')[0] == ('explicit-model',)
    catalog = CatalogAdapter()
    wrapped_catalog = ExtensionProviderStream(runtime, catalog)
    catalog.catalog = (catalog.catalog[1],)
    with pytest.raises(McpError, match='unavailable'):
        model_catalog(wrapped_catalog, 'explicit-model')


async def test_original_parallel_tool_results_keep_exact_order_and_do_not_execute_host_tools():
    from xdog.ai.types import ToolResultMessage

    from packages.agent.tests.test_mcp_sampling import paired_messages
    adapter = Adapter()
    source = request()
    source['params']['messages'] = paired_messages()
    executor = SamplingExecutor(adapter, ('explicit-model',), lambda: True)
    output = await executor(source, selection(), asyncio.Event())
    assert output['content']['text'] == 'MODEL OUTPUT' and len(adapter.calls) == 1
    history = adapter.calls[0][1].messages
    results = [message for message in history if isinstance(message, ToolResultMessage)]
    assert [message.tool_call_id for message in results] == ['2', '1']
    assert [message.tool_name for message in results] == ['two', 'one']
    assert [message.content[0].text for message in results] == ['SECOND', 'FIRST']
    assert adapter.calls[0][1].tools is None  # Prior transcript calls are not host execution authority.


async def test_provider_cannot_ignore_reviewed_tool_choice_none_to_return_proposal():
    adapter = Adapter(AssistantMessage(content=(ToolCall(id='proposal', name='approved', arguments={}),),
                                       stop_reason='toolUse'))
    executor = SamplingExecutor(adapter, ('explicit-model',), lambda: True)
    source = request()
    source['params'].update(tools=[{'name': 'approved', 'inputSchema': {'type': 'object'}}],
                            toolChoice={'mode': 'none'})
    with pytest.raises(McpError, match='tool choice'):
        await executor(source, {**selection(), 'tools': ['approved']}, asyncio.Event())
    assert len(adapter.calls) == 1 and adapter.calls[0][2].tool_choice.type == 'none'


async def test_provider_cannot_ignore_required_tools_to_return_text_only():
    adapter = Adapter()
    executor = SamplingExecutor(adapter, ('explicit-model',), lambda: True)
    source = request()
    source['params'].update(tools=[{'name': 'approved', 'inputSchema': {'type': 'object'}}],
                            toolChoice={'mode': 'required'})
    with pytest.raises(McpError, match='required tool'):
        await executor(source, {**selection(), 'tools': ['approved']}, asyncio.Event())
    assert len(adapter.calls) == 1 and adapter.calls[0][2].tool_choice.type == 'any'
