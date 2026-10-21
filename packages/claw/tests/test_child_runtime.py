"""Claw owns child execution; Agent supplies only SDK handles and protocol."""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import pytest
from xdog.agent import AgentTool, AgentToolResult
from xdog.agent.async_work import run_owned
from xdog.agent.skills.tool import _Reader
from xdog.agent.supervision import ChildClient
from xdog.agent.supervision_policy import ChildSkills
from xdog.ai.types import AssistantMessage, Model, StreamOptions, TextContent, ToolCall, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.child_runtime import ClawChildRuntime
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group


def application(tmp_path, stream, tools=('filesystem', 'current_time', 'skill', 'message')):
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/parent', stream_fn=stream)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace'), enabled_tools=tools))
    return app


async def prepared(app):
    return await app._runtimes['main'].prepare_session(allow_reset=False)


async def wait_for(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.002)


async def test_runtime_store_extension_ownership_and_sdk_parent_without_conversation_inheritance(tmp_path):
    calls, children = [], []
    owner_loop = asyncio.get_running_loop()
    def stream(model, context, options):
        assert asyncio.get_running_loop() is owner_loop
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='actual reply'),)))
    app = application(tmp_path, stream)
    try:
        session = await prepared(app)
        runtime = session.children.supervisor
        assert type(runtime) is ClawChildRuntime
        assert runtime.__class__.__module__.startswith('xdog.claw.')
        assert session.children.store.__class__.__module__.startswith('xdog.claw.')
        assert session.children.extensions.__class__.__module__.startswith('xdog.claw.')
        assert not hasattr(runtime, '_thread') and not hasattr(runtime, '_ready')
        original = runtime.factory
        async def factory(spec, publish):
            child = await original(spec, publish)
            children.append(child)
            assert child.parent is session.children.parent and child.parent.live
            assert not child.messages
            return child
        runtime.factory = factory
        session.agent.append_message(UserMessage(content='PARENT PRIVATE HISTORY'))
        session.agent.set_system_prompt('PARENT PRIVATE SYSTEM')
        async def parent_transform(messages):
            raise AssertionError('Parent context transform must not enter a child')
        session.agent.set_transform_context(parent_transform)
        client = ChildClient(runtime)
        child = await client.spawn('FIRST USER TASK', config=None)
        record = await child.wait()
        assert record['status'] == 'completed' and len(calls) == 1
        assert runtime._loop is owner_loop and not runtime.closed
        assert 'PARENT PRIVATE' not in str(calls[0])
        assert 'FIRST USER TASK' in str(calls[0].messages) and 'FIRST USER TASK' not in str(calls[0].system_prompt)
        assert children[0].parent is not None and session.agent.parent is None
        assert not session.children.snapshots and not session.children.grants
        await runtime.aclose()
        assert runtime.closed and owner_loop.is_running() and not owner_loop.is_closed()
        assert not children[0].parent.live
        assert not runtime.control_alive(runtime.tool_return_owner)
    finally:
        await app.stop()


async def test_legacy_instructions_are_first_user_material_and_not_system_authority(tmp_path):
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='done'),)))
    app = application(tmp_path, stream)
    try:
        session = await prepared(app)
        identifier = await session.children.supervisor.start('TASK KNOWLEDGE', config={
            'system_prompt': 'CHILD SYSTEM ROLE', 'instructions': 'LEGACY USER MATERIAL', 'tools': [], 'skills': []})
        record = await session.children.supervisor.control('wait', identifier)
        assert record['status'] == 'completed'
        assert 'CHILD SYSTEM ROLE' in str(calls[0].system_prompt)
        assert 'TASK KNOWLEDGE' not in str(calls[0].system_prompt)
        assert 'LEGACY USER MATERIAL' not in str(calls[0].system_prompt)
        assert 'LEGACY USER MATERIAL' in str(calls[0].messages[0].content)
        assert record['config']['instructions'] == 'LEGACY USER MATERIAL'
        assert session.children.store.load()[0]['config']['skills'] == []
    finally:
        await app.stop()


@pytest.mark.parametrize('selection', [['missing-tool'], ['agent']])
async def test_tool_grants_cannot_exceed_actual_parent_or_delegate_supervision(tmp_path, selection):
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    app = application(tmp_path, stream)
    try:
        session = await prepared(app)
        with pytest.raises(ValueError):
            await session.children.supervisor.start('must not run', config={'tools': selection})
        assert not calls and not session.children.supervisor.active
    finally:
        await app.stop()


async def test_frozen_actions_and_current_parent_tool_identity_are_both_enforced(tmp_path):
    executed, calls = [], []
    async def execute(identifier, args, *extra, **kwargs):
        executed.append(args)
        return AgentToolResult.from_text('executed')
    source = AgentTool(name='operation', parameters={'type': 'object', 'required': ['action'],
        'properties': {'action': {'enum': ['read', 'write']}}, 'additionalProperties': False}, execute=execute)
    def stream(model, context, options):
        calls.append(context)
        message = (AssistantMessage(content=(ToolCall(id='op', name='operation', arguments={'action': 'write'}),))
                   if context.messages[-1].role == 'user' else AssistantMessage(content=(TextContent(text='stopped'),)))
        return EventStream.empty(message)
    app = application(tmp_path, stream, tools=())
    try:
        session = await prepared(app)
        session.agent.set_tools((source, *session.children.supervisor.tools()))
        identifier = await session.children.supervisor.start('work', access='inherit',
            config={'tools': ['operation'], 'tool_actions': {'operation': ['read']}})
        record = await session.children.supervisor.control('wait', identifier)
        assert record['status'] == 'completed' and not executed
        assert 'outside delegated permissions' in str(record['messages'])
        # Revocation after admission but before the actual provider call must not
        # consume a model request or use a later same-named tool as authority.
        original = session.children.supervisor.factory
        async def revoke(spec, publish):
            child = await original(spec, publish)
            session.agent.set_tools((AgentTool(name='operation', parameters=source.parameters, execute=execute),))
            return child
        session.children.supervisor.factory = revoke
        before = len(calls)
        identifier = await session.children.supervisor.start('must not request', config={'tools': ['operation']})
        record = await session.children.supervisor.control('wait', identifier)
        assert record['status'] == 'failed' and len(calls) == before
        assert 'delegation changed' in record['error'] and not executed
    finally:
        await app.stop()


async def test_skills_are_selected_readonly_no_parent_manager_in_executor_context_and_off_loop_checks(tmp_path):
    calls, children, threads = [], [], []
    owner_thread = threading.get_ident()
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='done'),)))
    app = application(tmp_path, stream)
    try:
        session = await prepared(app)
        manager = session._runtime.skill_manager
        await run_owned(lambda: manager.save_skill(
            'selected', 'SELECTED BODY', name='selected', description='Selected'))
        await run_owned(lambda: manager.save_skill('other', 'OTHER PRIVATE BODY', name='other', description='Other'))
        original_load = _Reader.load
        def reading(reader, slug, stopped):
            threads.append(threading.get_ident())
            return original_load(reader, slug, stopped)
        original = session.children.supervisor.factory
        async def capture(spec, publish):
            child = await original(spec, publish)
            children.append(child)
            return child
        session.children.supervisor.factory = capture
        with patch.object(_Reader, 'load', reading):
            identifier = await session.children.supervisor.start('use chosen skill', config={'skills': ['selected']})
            record = await session.children.supervisor.control('wait', identifier)
        assert record['status'] == 'completed' and calls
        child = children[0]
        delegated = child.tool_context['_skill_manager']
        assert isinstance(delegated, ChildSkills) and delegated is not manager
        assert all(value is not manager for value in child.tool_context.values())
        assert delegated.slugs == ('selected',) and delegated.load_skill('other') is None
        assert 'SELECTED BODY' in str(calls[0].system_prompt) and 'OTHER PRIVATE BODY' not in str(calls[0])
        with pytest.raises(PermissionError):
            delegated.save_skill('forbidden', 'cannot mutate parent')
        assert threads and all(thread != owner_thread for thread in threads)
        with pytest.raises(ValueError, match='exceed parent'):
            await session.children.supervisor.start('missing skill', config={'skills': ['not-granted']})
        assert len(calls) == 1 and len(session.children.supervisor.snapshot()['children']) == 1
        assert await run_owned(manager.load_skill, 'forbidden') is None
    finally:
        await app.stop()


async def test_skill_revocation_before_first_provider_request_is_not_silent_fallback(tmp_path):
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    app = application(tmp_path, stream)
    try:
        session = await prepared(app)
        manager = session._runtime.skill_manager
        await run_owned(lambda: manager.save_skill('selected', 'ORIGINAL', name='selected', description='Selected'))
        original = session.children.supervisor.factory
        async def revoke(spec, publish):
            child = await original(spec, publish)
            await run_owned(manager.remove_skill, 'selected')
            return child
        session.children.supervisor.factory = revoke
        identifier = await session.children.supervisor.start(
            'no model after revocation', config={'skills': ['selected']})
        record = await session.children.supervisor.control('wait', identifier)
        assert record['status'] == 'failed' and not calls
        assert 'skill' in record['error'].lower() and any(
            word in record['error'].lower() for word in ('revoked', 'not available', 'changed'))
    finally:
        await app.stop()


async def test_child_model_metadata_and_native_compaction_archive_remain_on_host_owner(tmp_path):
    requests, summaries, owner = [], [], asyncio.get_running_loop()
    app = application(tmp_path, lambda *args: EventStream.empty(AssistantMessage()))
    try:
        session = await prepared(app)
        material = session._runtime.workspace_dir / 'material'
        await run_owned(material.write_text, 'FINDINGS\n' + 'evidence ' * 600)
        class Adapter:
            def resolve_model(self, name):
                return Model(id='test/child', context_window=14000, max_prompt_tokens=12000, max_tokens=128,
                             reasoning=True, supported_efforts=('low',), supports_tool_calls=True)
            def __call__(self, model, context, options):
                assert asyncio.get_running_loop() is owner
                assert model == 'test/child' and options.thinking == 'low' and options.max_tokens == 128
                if not context.tools:
                    assert list(session.children.store.root.glob('*.compaction-*.json'))
                    summaries.append(context)
                    return EventStream.empty(AssistantMessage(content=(TextContent(text='findings retained'),)))
                requests.append(context)
                return EventStream.empty(AssistantMessage(content=(
                    (ToolCall(id=f'read-{len(requests)}', name='filesystem',
                              arguments={'action': 'read', 'path': 'material'}),)
                    if len(requests) < 26 else (TextContent(text='finished'),))))
        session.agent.set_stream_fn(Adapter())
        session.agent.set_options(StreamOptions(max_tokens=128))
        identifier = await session.children.supervisor.start('CURRENT GOAL', access='read-only',
            config={'model': 'test/child', 'thinking': 'low', 'tools': ['filesystem']})
        record = await session.children.supervisor.control('wait', identifier)
        assert record['status'] == 'completed', record['error']
        assert len(requests) == 26 and summaries
        archives = await run_owned(session.children.store.compaction_archives, identifier)
        assert len(archives) == len(summaries) and 'FINDINGS' in str(archives)
        assert record['effective']['model'] == 'test/child' and record['effective']['thinking'] == 'low'
        assert not session.agent.messages
    finally:
        await app.stop()


async def test_skills_are_rejected_before_id_registry_store_begin_or_factory(tmp_path):
    calls, factories = [], []
    app = application(tmp_path, lambda *args: calls.append(args))
    try:
        session = await prepared(app)
        runtime = session.children.supervisor
        original = runtime.factory
        async def factory(spec, publish):
            factories.append(spec)
            return await original(spec, publish)
        runtime.factory = factory
        before = set(session.children.store.root.glob('*'))
        with pytest.raises(ValueError, match='exceed parent'):
            await runtime.start('no admission', config={'skills': ['not-in-parent']})
        assert not runtime.active and runtime.snapshot()['children'] == []
        assert not factories and not calls
        assert not session.children.snapshots and not session.children.grants and not session.children.skill_grants
        assert set(session.children.store.root.glob('*')) == before
    finally:
        await app.stop()


async def test_closing_during_owned_skill_admission_waits_for_io_without_registry_or_model(tmp_path):
    calls = []
    app = application(tmp_path, lambda *args: calls.append(args))
    entered, release = threading.Event(), threading.Event()
    work = closing = None
    try:
        session = await prepared(app)
        manager = session._runtime.skill_manager
        await run_owned(lambda: manager.save_skill('selected', 'BODY', name='selected', description='Selected'))
        original_load = _Reader.load
        def blocked(reader, slug, stopped):
            entered.set()
            assert release.wait(5)
            return original_load(reader, slug, stopped)
        runtime = session.children.supervisor
        before = set(session.children.store.root.glob('*'))
        with patch.object(_Reader, 'load', blocked):
            work = asyncio.create_task(runtime.start('no admitted worker', config={'skills': ['selected']}))
            assert await asyncio.to_thread(entered.wait, 3)
            assert runtime.active and not runtime.snapshot()['children'] and not session.children.snapshots
            closing = asyncio.create_task(runtime.aclose())
            await asyncio.sleep(.02)
            assert not closing.done() and not work.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await work
            await closing
        assert runtime.closed and not runtime.active and not calls
        assert set(session.children.store.root.glob('*')) == before
        assert not session.children.snapshots and not session.children.skill_grants
        assert asyncio.get_running_loop().is_running()
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await app.stop()


async def test_unpublished_or_replaced_parent_rejects_admission_and_context_is_explicit_allowlist(tmp_path):
    children = []
    def stream(*args):
        return EventStream.empty(AssistantMessage(content=(TextContent(text='done'),)))
    app = application(tmp_path, stream)
    try:
        session = await prepared(app)
        owner = session._runtime
        assert owner._active_session is session and session.children.parent.live
        owner._active_session = None
        try:
            with pytest.raises(ValueError, match='parent owner retired'):
                await session.children.supervisor.start('not published')
        finally:
            owner._active_session = session
        secret = object()
        session.agent.set_tool_context({**session.agent.tool_context, '_future_session_private': secret,
                                       'future_business_reference': secret})
        original = session.children.supervisor.factory
        async def factory(spec, publish):
            child = await original(spec, publish)
            children.append(child)
            return child
        session.children.supervisor.factory = factory
        identifier = await session.children.supervisor.start('explicit context only', config={'tools': []})
        assert (await session.children.supervisor.control('wait', identifier))['status'] == 'completed'
        context = children[0].tool_context
        assert set(context) == {'group_id', 'workspace_dir', 'fs_workspace', 'data_dir', '_skill_manager'}
        assert all(value is not secret for value in context.values())
        assert '_send_fn' not in context and '_memory_manager' not in context and '_memory_search' not in context
    finally:
        await app.stop()


@pytest.mark.parametrize('scenario', ['unknown', 'reasoning', 'image', 'withdrawn', 'no-tools'])
async def test_explicit_model_capabilities_are_real_and_never_fall_back_or_change_parent(tmp_path, scenario):
    from xdog.ai.types import ImageContent
    calls = []
    class Adapter:
        metadata = Model(id='test/child', context_window=64000, max_tokens=128, reasoning=True,
                         supported_efforts=('low',), input=('text',), supports_tool_calls=scenario != 'no-tools')
        def resolve_model(self, name):
            if name == 'test/child':
                return self.metadata
            if name == 'test/parent':
                return Model(id='test/parent', context_window=128000, max_tokens=1024)
            return None
        def __call__(self, model, context, options):
            calls.append((model, context, options))
            assert model == 'test/child' and options.thinking == 'low' and options.max_tokens == 128
            if scenario == 'no-tools':
                assert not context.tools
            return EventStream.empty(AssistantMessage(content=(TextContent(text='metadata-backed answer'),)))
    adapter = Adapter()
    app = application(tmp_path, adapter)
    try:
        session = await prepared(app)
        session.agent.set_options(StreamOptions(max_tokens=1024, thinking='high'))
        before_model, before_options = session.agent.state.model, session.agent.options
        original = session.children.supervisor.factory
        if scenario == 'withdrawn':
            async def withdraw(spec, publish):
                child = await original(spec, publish)
                adapter.metadata = None
                return child
            session.children.supervisor.factory = withdraw
        config = {'model': 'test/absent' if scenario == 'unknown' else 'test/child',
                  'thinking': 'high' if scenario == 'reasoning' else 'low', 'tools': ['filesystem']}
        images = ((ImageContent(data=('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE'
                                    'QVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), mime_type='image/png'),)
                  if scenario == 'image' else ())
        identifier = await session.children.supervisor.start('Explicit child model', config=config, images=images)
        record = await session.children.supervisor.control('wait', identifier)
        if scenario == 'no-tools':
            assert record['status'] == 'completed', record['error']
            assert len(calls) == 1 and record['effective']['supports_tool_calls'] is False
        else:
            assert record['status'] == 'failed' and not calls
            assert any(word in record['error'].lower() for word in
                       ('unavailable', 'advertised', 'image', 'metadata', 'vision', 'fallback'))
        assert session.agent.state.model == before_model and session.agent.options == before_options
        assert not session.agent.messages
    finally:
        await app.stop()


async def test_independent_child_keeps_own_reasoning_signatures_and_reports_tool_prose_as_received(tmp_path):
    from xdog.ai.types import ThinkingContent
    calls, facts = [], []
    async def execute(*args, **kwargs):
        return AgentToolResult.from_text('INTERNAL TOOL RESULT')
    source = AgentTool(name='inspect', parameters={'type': 'object'}, execute=execute)
    class Adapter:
        def resolve_model(self, name):
            return (Model(id=name, context_window=64000, max_tokens=128, reasoning=True,
                          supported_efforts=('low',)) if name in {'test/parent', 'test/child'} else None)
        def __call__(self, model, context, options):
            assert model == 'test/child'
            calls.append(context)
            content = ((ThinkingContent(thinking='CHILD THOUGHT', thinking_signature='CHILD-THOUGHT-SIGNATURE'),
                        TextContent(text='Progress before tool'),
                        ToolCall(id='inspect-1', name='inspect', arguments={},
                                 thought_signature='CHILD-CALL-SIGNATURE'))
                       if len(calls) == 1 else (TextContent(text='Actual final child answer'),))
            return EventStream.empty(AssistantMessage(content=content))
    app = application(tmp_path, Adapter(), tools=())
    try:
        session = await prepared(app)
        session.agent.set_tools((source, *session.children.supervisor.tools()))
        remove = session.children.supervisor.subscribe(facts.append)
        try:
            identifier = await session.children.supervisor.start('use inspect', access='inherit',
                config={'model': 'test/child', 'thinking': 'low', 'tools': ['inspect']})
            record = await session.children.supervisor.control('wait', identifier)
        finally:
            remove()
        assert record['status'] == 'completed' and len(calls) == 2
        assert 'CHILD-THOUGHT-SIGNATURE' in str(calls[1].messages)
        assert 'CHILD-CALL-SIGNATURE' in str(calls[1].messages)
        correspondence = [fact for fact in facts if fact['type'] == 'child_message']
        replies = [fact for fact in correspondence if fact['direction'] == 'child_to_parent']
        assert [(fact['status'], fact['text']) for fact in replies] == [
            ('received', 'Progress before tool'), ('completed', 'Actual final child answer')]
        assert all('INTERNAL TOOL RESULT' not in fact['text'] and 'CHILD THOUGHT' not in fact['text']
                   for fact in correspondence)
    finally:
        await app.stop()


def test_synchronous_runtime_construction_and_close_do_not_acquire_or_start_a_loop():
    seen = []
    runtime = ClawChildRuntime(lambda spec, publish: seen.append(spec), owner_loop=lambda: None)
    assert runtime._loop is None and runtime.snapshot()['children'] == []
    assert not runtime.active and not runtime.closed and not seen
    runtime.close()
    assert runtime.closed and runtime._loop is None and not seen


@pytest.mark.parametrize('revocation', ['tools', 'read-only', 'mcp'])
async def test_parallel_prepared_calls_recheck_real_effect_policy_after_first_executor_revokes(tmp_path, revocation):
    writes, calls, mcp_allowed = [], [], [True]
    session = None
    async def revoke(*args, **kwargs):
        assert session is not None
        if revocation == 'tools':
            session.agent.set_tools((first, *session.children.supervisor.tools()))
        elif revocation == 'read-only':
            session.access.set_read_only(True)
        else:
            mcp_allowed[0] = False
        return AgentToolResult.from_text('policy revoked before next executor')
    async def write(*args, **kwargs):
        writes.append('UNAUTHORIZED EFFECT')
        return AgentToolResult.from_text('must not execute')
    first = AgentTool(name='revoke', parameters={'type': 'object'}, execute=revoke)
    second = AgentTool(name='mutation', parameters={'type': 'object'}, execute=write)
    def stream(model, context, options):
        calls.append(context)
        content = ((ToolCall(id='first', name='revoke', arguments={}),
                    ToolCall(id='second', name='mutation', arguments={})) if len(calls) == 1
                   else (TextContent(text='No more tools'),))
        return EventStream.empty(AssistantMessage(content=content))
    app = application(tmp_path, stream, tools=())
    try:
        session = await prepared(app)
        session.agent.set_tools((first, second, *session.children.supervisor.tools()))
        with (patch.object(session._runtime.mcp, 'tools', lambda: (second,) if revocation == 'mcp' else ()),
              patch.object(session._runtime.mcp, 'allowed', lambda name: mcp_allowed[0])):
            identifier = await session.children.supervisor.start('parallel work', access='inherit',
                config={'tools': ['revoke', 'mutation']})
            record = await session.children.supervisor.control('wait', identifier)
        assert not writes
        assert 'policy revoked before next executor' in str(record['messages'])
        assert any(word in str(record['messages']).lower()
                   for word in ('read-only', 'disabled', 'delegation', 'policy'))
    finally:
        await app.stop()


async def test_delegated_schema_advertisement_and_execution_keep_original_snapshot(tmp_path):
    executed, calls, children = [], [], []
    async def execute(*args, **kwargs):
        executed.append(True)
        return AgentToolResult.from_text('must not run')
    source = AgentTool(name='restricted', parameters={'type': 'object', 'required': ['action'],
        'properties': {'action': {'enum': ['read']}}, 'additionalProperties': False}, execute=execute)
    def stream(model, context, options):
        calls.append(context)
        if len(calls) == 1:
            advertised = next(tool for tool in context.tools if tool.name == 'restricted')
            assert advertised.parameters['properties']['action']['enum'] == ['read']
            return EventStream.empty(AssistantMessage(content=(
                ToolCall(id='restricted-call', name='restricted', arguments={'action': 'write'}),)))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='denied'),)))
    app = application(tmp_path, stream, tools=())
    try:
        session = await prepared(app)
        session.agent.set_tools((source, *session.children.supervisor.tools()))
        original = session.children.supervisor.factory
        async def mutate(spec, publish):
            child = await original(spec, publish)
            children.append(child)
            source.parameters['properties']['action']['enum'].append('write')
            return child
        session.children.supervisor.factory = mutate
        identifier = await session.children.supervisor.start('read only schema', access='inherit',
            config={'tools': ['restricted']})
        record = await session.children.supervisor.control('wait', identifier)
        assert record['status'] == 'completed' and not executed
        assert len(calls) == 2
        assert children[0].state.tools[0].parameters is not source.parameters
        assert 'write' not in children[0].state.tools[0].parameters['properties']['action']['enum']
    finally:
        await app.stop()
