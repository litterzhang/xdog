"""Selection proposals reuse the actual producer barrier; no midbatch mutation or child borrowing."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool
from xdog.agent.extensions.active_tools import snapshot
from xdog.agent.extensions.active_tools_request import AfterRunToolSelections, available
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import validate_dialog
from xdog.agent.extensions.invocation import InvocationLease, _current
from xdog.agent.extensions.runtime import ExtensionRuntime, silent_extension_ui
from xdog.agent.extensions.tool_inventory import capture
from xdog.ai.types import AssistantMessage, StartEvent, TextContent, ToolCall, UserMessage
from xdog.ai.utils.event_stream import EventStream


def dialog(**changes):
    return {'id': 'proposal', 'kind': 'session', 'operation': 'select_tools', 'title': 'Review exact tools',
            'after_run': True, 'names': ['safe'], 'revision': 0, **changes}


def current():
    return {'session_id': 'original', 'revision': 0, 'configured': ['safe', 'other'], 'active': ['safe', 'other']}


def setup(tmp_path, **settings):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/aftertools', root / 'aftertools')
    return ExtensionRuntime(['aftertools'], root, tmp_path, settings={'aftertools': settings},
                            context=lambda: {'session_id': 'original'})


async def until(predicate):
    async with asyncio.timeout(8):
        while not predicate():
            await asyncio.sleep(.003)


@pytest.mark.parametrize('method,allowed', [('tools/call', True), ('xdog/pre', True), ('xdog/post', True),
    ('xdog/input', True), ('xdog/event', True), ('xdog/context_finish', True), ('xdog/command', False),
    ('xdog/provider_start', False), ('xdog/provider_auth', False), ('xdog/terminal_input', False),
    ('xdog/custom_frame', False)])
def test_named_selection_callbacks_do_not_gain_other_surfaces(method, allowed):
    assert available(method, {}) is allowed
    assert not available('xdog/context_finish', {'operation': 'compaction'})


@pytest.mark.parametrize('changes', [{'after_run': False}, {'after_run': 1}, {'names': ['safe', 'safe']},
    {'revision': True}, {'target': {}}, {'session_id': 'foreign'},
    {'tool_selection_run': {'id': 'x', 'session_id': 'original', 'method': []}},
    {'active_tools_after_run_generation': True}])
def test_after_run_schema_rejects_forged_types_and_scope_fields(changes):
    with pytest.raises(ValueError):
        validate_dialog(dialog(**changes))


@pytest.mark.parametrize('ending', ['success', 'denied', 'callback-failed', 'producer-failed', 'revoked',
                                    'child', 'changed-revision', 'dropped-name', 'forged-current'])
async def test_review_callback_and_real_producer_settlement_are_separate_once_only_barriers(ending):
    queue, reviews = AfterRunToolSelections(), []
    state = {'alive': True, 'settled': False, 'success': True}
    lease = InvocationLease('aftertools', 'original', 'child' if ending == 'child' else '', 'tools/call',
                            lambda: state['alive'], object())
    token = _current.set(lease)
    proposal = dialog()
    if ending == 'changed-revision':
        proposal['revision'] = 1
    if ending == 'dropped-name':
        proposal['names'] = ['removed']
    if ending == 'forged-current':
        proposal['current_tools'] = current()
    async def ask(extension, projected):
        assert validate_dialog(projected) == projected
        assert projected['current_tools'] == current()
        reviews.append(projected)
        return ending != 'denied'
    try:
        if ending == 'forged-current':
            with pytest.raises(ValueError):
                await queue.reviewed('aftertools', proposal, producer=state, ask=ask, current=current(),
                    alive=lambda: state['alive'], settled=lambda: state['settled'], successful=lambda: state['success'])
            return
        result = await queue.reviewed('aftertools', proposal, producer=state, ask=ask, current=current(),
            alive=lambda: state['alive'], settled=lambda: state['settled'], successful=lambda: state['success'])
        assert result is (ending not in {'denied', 'child', 'changed-revision', 'dropped-name'})
        assert queue.take() is None
        await lease.complete(ending != 'callback-failed')
        assert queue.take() is None  # Successful callback is NOT an idle producer.
        state.update(settled=True, alive=ending != 'revoked', success=ending != 'producer-failed')
        value = queue.take()
        assert (value is not None) is (ending == 'success')
        if value is not None:
            assert value.names == ('safe',) and value.revision == 0
        assert queue.take() is None
    finally:
        _current.reset(token)


@pytest.mark.parametrize('scope', ['tool', 'pre', 'post', 'input', 'event', 'context'])
async def test_actual_worker_allowed_hook_scopes_stage_only_without_tool_mutation(tmp_path, scope):
    runtime = setup(tmp_path, scope=scope)
    queue, state, reviews = AfterRunToolSelections(), {'settled': False}, []
    async def ask(extension, projected):
        reviews.append(projected)
        return True
    async def handler(extension, value):
        return await queue.reviewed(extension, value, producer=state, ask=ask, current=current(),
            alive=lambda: True, settled=lambda: state['settled'], successful=lambda: True)
    runtime.active_tools_request_handler = handler
    try:
        await runtime.initialize()
        if scope == 'tool':
            assert (await runtime.tools()[0].execute('call', {})).content[0].text == 'TOOL-SELECTION-True'
        elif scope == 'pre':
            assert await runtime.pre('safe', {}) == {}
        elif scope == 'post':
            assert await runtime.post('safe', {}, 'unchanged') == 'unchanged'
        elif scope == 'event':
            await runtime.event('turn_start')
        elif scope == 'input':
            message = UserMessage(content='unchanged')
            assert await runtime.input(message) == message
        else:
            assert await runtime.transform([]) == []
        assert len(reviews) == 1 and queue.take() is None
        state['settled'] = True
        assert queue.take().names == ('safe',)
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('ending', ['isError', 'raised', 'cancel', 'child', 'headless', 'terminal', 'command'])
async def test_actual_failure_and_unsupported_scope_do_not_stage_parent_selection(tmp_path, ending):
    marker, release = tmp_path / 'marker', tmp_path / 'release'
    runtime = setup(tmp_path, **({'marker': str(marker), 'release': str(release)} if ending == 'cancel' else {}))
    queue, state, reviews = AfterRunToolSelections(), {'settled': False}, []
    if ending == 'child':
        runtime.context = lambda: {'session_id': 'original', 'child_id': '0123456789ab'}
    async def ask(extension, projected):
        reviews.append(projected)
        return True
    async def handler(extension, value):
        return await queue.reviewed(extension, value, producer=state, ask=ask, current=current(),
            alive=lambda: True, settled=lambda: state['settled'], successful=lambda: True)
    runtime.active_tools_request_handler = handler
    task = None
    try:
        await runtime.initialize()
        if ending == 'terminal':
            assert await runtime.terminal_input('x', kind='key', surface='prompt') == {'consume': False, 'data': 'x'}
        elif ending == 'command':
            assert await runtime.command('ext-aftertools-select', '') == 'TOOL-SELECTION-False'
        elif ending == 'headless':
            with silent_extension_ui():
                assert (await runtime.tools()[0].execute('call', {})).content[0].text == 'TOOL-SELECTION-False'
        else:
            task = asyncio.create_task(runtime.tools()[0].execute('call',
                {'error': ending == 'isError', 'fail': ending == 'raised'}))
            if ending == 'cancel':
                await until(marker.exists)
                task.cancel()
                release.touch()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif ending == 'raised':
                with pytest.raises(Exception, match='callback|Extension'):
                    await task
            else:
                result = await task
                assert result.is_error is (ending == 'isError')
        state['settled'] = True
        assert queue.take() is None
    finally:
        release.touch()
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()


async def test_get_active_tools_reuses_captured_original_names_without_a_new_host_dialog():
    api = ExtensionAPI({})
    assert await api.get_active_tools() is None
    safe = AgentTool(name='safe', description='safe', parameters={})
    agent = Agent(lambda *args: EventStream.empty(AssistantMessage()),
                  config=AgentConfig(model='fixture'), tools=[safe])
    token = api._tool_inventory.set(capture(agent, 'original', 'child-owner'))
    try:
        value = await api.get_active_tools()
        assert value == snapshot(agent, 'original')
        value['active'].clear()
        assert (await api.get_active_tools())['active'] == ['safe']
        assert not await api.set_active_tools([], value['revision'])
    finally:
        api._tool_inventory.reset(token)


async def test_actual_agent_tool_callback_cannot_apply_before_entire_original_producer_cleanup(tmp_path):
    runtime = setup(tmp_path)
    queue, calls = AfterRunToolSelections(), []
    model_wait, model_finish, producer_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
    success = [False]
    producer = None
    async def safe(*args, **kwargs):
        raise AssertionError('Selection must not execute tools')
    safe_tool = AgentTool(name='safe', description='Safe', parameters={}, execute=safe)
    def stream(model, context, options):
        calls.append(context)
        if len(calls) == 1:
            return EventStream.empty(AssistantMessage(content=(ToolCall(id='select',
                name=next(tool.name for tool in context.tools if tool.name.startswith('ext_aftertools_select')),
                arguments={}),)))
        async def generate():
            model_wait.set()
            yield StartEvent(partial=AssistantMessage())
            await model_finish.wait()
            from xdog.ai.types import DoneEvent
            yield DoneEvent(message=AssistantMessage(content=(TextContent(text='DONE'),)))
        final = asyncio.get_running_loop().create_future()
        final.set_result(AssistantMessage(content=(TextContent(text='DONE'),)))
        return EventStream.from_async_generator(generate(), result_future=final)
    await runtime.initialize()
    agent = Agent(stream, config=AgentConfig(model='fixture'), tools=[safe_tool, *runtime.tools()])
    runtime.tool_inventory = lambda: capture(agent, 'original', 'original')
    revision, configured = agent.tool_selection_revision, tuple(tool.name for tool in agent.state.tools)
    async def ask(extension, projected):
        assert agent.is_streaming and projected['current_tools']['configured'] == list(configured)
        return True
    async def handler(extension, value):
        return await queue.reviewed(extension, value, producer=producer, ask=ask, current=snapshot(agent, 'original'),
            alive=lambda: agent.tool_selection_revision == revision
                and tuple(tool.name for tool in agent.state.tools) == configured,
            settled=lambda: producer.done() and not agent.is_streaming
                and (agent._producer_task is None or agent._producer_task.done()), successful=lambda: success[0])
    runtime.active_tools_request_handler = handler
    async def run():
        events = await agent.prompt('Original task')
        await events.collect()
        await agent.wait_for_idle()
        await producer_cleanup.wait()  # The outer producer still owns cleanup even after Agent idle.
        success[0] = True
    producer = asyncio.create_task(run())
    try:
        await asyncio.wait_for(model_wait.wait(), 8)
        assert queue.take() is None and agent.active_tool_selection is None
        with pytest.raises(RuntimeError, match='idle'):
            agent.set_active_tools(['safe'], expected_revision=revision)
        model_finish.set()
        await until(lambda: not agent.is_streaming)
        assert not producer.done() and queue.take() is None
        producer_cleanup.set()
        await producer
        item = queue.take()
        assert item is not None and item.names == ('safe',)
        agent.set_active_tools(item.names, expected_revision=item.revision)
        assert agent.active_tool_selection == ('safe',) and len(calls) == 2
    finally:
        model_finish.set()
        producer_cleanup.set()
        await agent.cancel_and_wait()
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        await runtime.aclose()
