"""Real Coding owner, independent prompt/skills, live delegation and approval fences."""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace

import pytest
from xdog.agent import AgentTool, AgentToolResult
from xdog.agent.skills.manager import SkillManager
from xdog.agent.skills.tool import create_skill_tool
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.child_runtime import CodingChildRuntime
from xdog.coding.core.permissions import PermissionManager

from packages.coding.tests.test_child_agents import create, wait


async def close(session):
    await session.children.runtime.aclose()
    await session.extensions.aclose()
    session.dispose()


def install_skills(session, tmp_path):
    root = tmp_path / 'skills'
    root.mkdir()
    for slug, body in [('review', 'CHILD REVIEW KNOWLEDGE'), ('private', 'PARENT PRIVATE KNOWLEDGE')]:
        directory = root / slug
        directory.mkdir()
        (directory / 'SKILL.md').write_text(f'---\nname: {slug}\ndescription: {slug} description\n---\n{body}')
    manager = SkillManager(root, packaged={})
    session.agent._skills = manager
    session.agent.set_tools((*session.agent.state.tools, create_skill_tool(manager)))
    return manager, root


async def test_coding_owns_runtime_and_child_keeps_no_parent_conversation_transform_or_tool_context(tmp_path):
    session, contexts = create(tmp_path)
    assert isinstance(session.children.runtime, CodingChildRuntime)
    assert session.children.runtime._thread is None
    session.agent.set_system_prompt('PARENT SYSTEM SECRET')
    session.agent.append_message(UserMessage(content='PARENT HISTORY SECRET'))
    calls, agents, events = [], [], []
    async def parent_context(messages):
        calls.append('context')
        return [*messages, UserMessage(content='PARENT TRANSFORM SECRET')]
    async def parent_input(message, source, cancel):
        calls.append('input')
        return UserMessage(content='PARENT INPUT SECRET')
    session._base_context_transform = parent_context
    session._base_input_transform = parent_input
    session.agent.tool_context['private_parent_object'] = object()
    original = session.children.create
    async def factory(spec, publish):
        child = await original(spec, publish)
        agents.append(child)
        return child
    session.children.runtime.factory = factory
    session.children.runtime.subscribe(events.append)
    try:
        identifier = await session.children.runtime.start('TASK KNOWLEDGE', config={
            'system_prompt': 'INDEPENDENT CHILD ROLE', 'instructions': 'LEGACY TASK MATERIALS', 'tools': []})
        record = await session.children.runtime.control('wait', identifier)
        assert record['status'] == 'completed', record['error']
        assert not calls and len(contexts) == 1 and len(contexts[0].messages) == 1
        assert 'INDEPENDENT CHILD ROLE' in str(contexts[0].system_prompt)
        assert 'LEGACY TASK MATERIALS' not in str(contexts[0].system_prompt)
        assert 'TASK KNOWLEDGE' in str(contexts[0].messages[0].content)
        assert 'LEGACY TASK MATERIALS' in str(contexts[0].messages[0].content)
        assert 'PARENT ' not in str(contexts[0])
        child = agents[0]
        assert child.parent is not None and child.parent.live
        assert not hasattr(child.parent, 'messages') and not hasattr(child.parent, 'system_prompt')
        assert child.tool_context == {'fs_workspace': str(tmp_path)}
        assert child.tool_context is not session.agent.tool_context
        assert session.agent.messages[0].content == 'PARENT HISTORY SECRET'
        facts = [row for row in events if row['type'] == 'child_message']
        assert [row['direction'] for row in facts] == ['parent_to_child', 'child_to_parent']
        assert 'LEGACY TASK MATERIALS' in facts[0]['text'] and facts[0]['status'] == 'admitted'
        assert facts[1]['text'] == 'child done' and facts[1]['status'] == 'completed'
    finally:
        await close(session)


@pytest.mark.parametrize('selected,expected', [([], ()), (['review'], ('review',)), (None, ('private', 'review'))])
async def test_actual_child_skill_catalog_is_selected_readonly_and_never_copies_parent_active_prompt(
        tmp_path, selected, expected):
    session, contexts = create(tmp_path, 'allow-all')
    manager, _ = install_skills(session, tmp_path)
    session.agent.set_active_skills(['private'])
    agents = []
    original = session.children.create
    async def factory(spec, publish):
        agent = await original(spec, publish)
        agents.append(agent)
        return agent
    session.children.runtime.factory = factory
    try:
        identifier = await session.children.runtime.start('task', access='inherit', config={'skills': selected})
        record = await session.children.runtime.control('wait', identifier)
        assert record['status'] == 'completed', record['error']
        catalog = agents[0]._skills
        assert catalog is not manager and tuple(sorted(catalog.slugs)) == expected
        assert tuple(sorted(row.slug for row in catalog.list_skills())) == expected
        assert (catalog.load_skill('review') is not None) == ('review' in expected)
        with pytest.raises(PermissionError):
            catalog.save_skill('new', 'cannot write parent catalog')
        if 'private' not in expected:
            assert 'PARENT PRIVATE KNOWLEDGE' not in str(contexts[0].system_prompt)
        if 'review' in expected:
            assert 'CHILD REVIEW KNOWLEDGE' in str(contexts[0].system_prompt)
        assert session.agent.active_skills == ('private',)
    finally:
        await close(session)


async def test_unknown_skill_fails_before_any_child_model_call(tmp_path):
    session, contexts = create(tmp_path)
    install_skills(session, tmp_path)
    try:
        with pytest.raises(ValueError, match='catalog'):
            await session.children.runtime.start('task', config={'skills': ['unavailable']})
        assert not contexts and not session.children.runtime.snapshot()['children']
        assert not session.children.store.root.exists()
        assert not session.children.parents and not session.children.snapshots and not session.children.skill_limits
    finally:
        await close(session)


async def test_coding_action_subset_blocks_mutation_without_widening_parent_policy(tmp_path):
    session, contexts = create(tmp_path, 'allow-all')
    try:
        identifier = await session.children.runtime.start('write', access='inherit', config={
            'tools': ['filesystem'], 'tool_actions': {'filesystem': ['read', 'ls']}})
        record = await session.children.runtime.control('wait', identifier)
        assert record['status'] == 'completed' and not (tmp_path / 'target').exists()
        assert 'outside delegated permissions' in str(record['messages'])
        assert contexts and session.permissions.mode == 'allow-all'
        assert record['config']['tool_actions'] == {'filesystem': ['read', 'ls']}
    finally:
        await close(session)


@pytest.mark.parametrize('change', ['permission_epoch', 'tool_identity', 'permission_owner'])
async def test_waiting_child_approval_cannot_cross_parent_epoch_identity_or_manager_change(tmp_path, change):
    session, _ = create(tmp_path)
    requests = []
    session.children.approver = lambda child, request: requests.append(request)
    try:
        identifier = await session.children.runtime.start('write', access='inherit')
        await wait(lambda: bool(requests))
        if change == 'permission_epoch':
            session.permissions.set_mode('deny')
            session.permissions.set_mode('ask')
        elif change == 'permission_owner':
            session.permissions = PermissionManager('allow-all')
        else:
            session.agent.set_tools(tuple(replace(tool) if tool.name == 'filesystem' else tool
                                          for tool in session.agent.state.tools))
        assert session.children.resolve(identifier, requests[0].id, 'allow_once')
        record = await session.children.runtime.control('wait', identifier)
        assert not (tmp_path / 'target').exists()
        assert ('policy changed' in str(record['messages']) or 'owner retired' in str(record['messages'])
                or 'original/current parent delegation' in str(record['messages']))
        assert not session.children.permissions
    finally:
        await close(session)


@pytest.mark.parametrize('change', ['owner', 'skill'])
async def test_model_boundary_rechecks_actual_parent_and_skill_revocation_before_followup(tmp_path, change):
    session, _ = create(tmp_path, 'allow-all')
    _, root = install_skills(session, tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def stream(model, context, options):
        calls.append(context)
        message = AssistantMessage(content=(TextContent(text='first result'),))
        completed = asyncio.get_running_loop().create_future()
        completed.set_result(message)
        async def generate():
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.002)
            yield DoneEvent(message=message)
        return EventStream.from_async_generator(generate(), result_future=completed)
    session.agent.set_stream_fn(stream)
    try:
        identifier = await session.children.runtime.start('task', config={'skills': ['review']})
        assert await asyncio.to_thread(entered.wait, 3)
        await session.children.runtime.control('follow-up', identifier, 'must not enter a revoked model')
        if change == 'owner':
            session.children._closed = True
        else:
            (root / 'review/SKILL.md').write_text('---\nname: review\n---\nREPLACED CONTENT')
        release.set()
        record = await session.children.runtime.control('wait', identifier)
        assert record['status'] == 'failed' and len(calls) == 1
        assert 'retired' in record['error'] if change == 'owner' else 'changed' in record['error']
    finally:
        release.set()
        await close(session)


async def test_failed_unadmitted_legacy_task_image_recovery_keeps_all_materials(tmp_path):
    from xdog.ai.types import ImageContent

    from packages.coding.tests.extension_session_helpers import PNG
    session, contexts = create(tmp_path)
    original = session.children.create
    async def unavailable(spec, publish):
        raise RuntimeError('factory unavailable before first input admission')
    session.children.runtime.factory = unavailable
    try:
        identifier = await session.children.runtime.start('TASK', config={'instructions': 'LEGACY MATERIALS'},
            images=(ImageContent(data=PNG, mime_type='image/png'),))
        record = await session.children.runtime.control('wait', identifier)
        assert record['status'] == 'failed' and not contexts
        queue = (await session.children.runtime.control('queue', identifier, '{"action":"list"}'))['queue']
        initial = queue['inputs'][0]
        recovered = await session.children.runtime.control('queue', identifier, json.dumps({
            'action': 'recover_task', 'id': initial['id'], 'revision': queue['revision']}))
        assert 'TASK' in recovered['queue']['recovered']['content']
        assert 'LEGACY MATERIALS' in recovered['queue']['recovered']['content']
        assert recovered['image_recovery']['images'][0]['data'] == PNG
        assert 'LEGACY MATERIALS' in recovered['image_recovery']['receipt']['content']
        assert not contexts and record['messages'] == []
    finally:
        session.children.runtime.factory = original
        await close(session)


async def test_parallel_prepared_call_cannot_execute_old_approval_after_later_call_waits(tmp_path):
    session, _ = create(tmp_path)
    requests = []
    session.children.approver = lambda child, request: requests.append(request)
    def stream(model, context, options):
        if context.messages[-1].role == 'user':
            message = AssistantMessage(content=tuple(ToolCall(id=name, name='filesystem', arguments={
                'action': 'write', 'path': name, 'content': 'must not write'}) for name in ('first', 'second')))
        else:
            message = AssistantMessage(content=(TextContent(text='done'),))
        return EventStream.empty(message)
    session.agent.set_stream_fn(stream)
    try:
        identifier = await session.children.runtime.start('work', access='inherit')
        await wait(lambda: len(requests) == 1)
        assert session.children.resolve(identifier, requests[0].id, 'allow_once')
        await wait(lambda: len(requests) == 2)
        session.permissions.set_mode('deny')
        session.permissions.set_mode('ask')
        assert session.children.resolve(identifier, requests[1].id, 'allow_once')
        record = await session.children.runtime.control('wait', identifier)
        assert record['status'] == 'completed', record['error']
        assert not (tmp_path / 'first').exists() and not (tmp_path / 'second').exists()
        assert 'policy changed before child tool execution' in str(record['messages'])
    finally:
        await close(session)


@pytest.mark.parametrize('outcome', ['pause', 'close', 'cancel'])
async def test_admission_catalog_io_has_no_child_registry_begin_or_lock_and_settles_before_shutdown(
        tmp_path, monkeypatch, outcome):
    from xdog.agent.child_policy import ParentHandle
    session, contexts = create(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = ParentHandle.capture_skills
    def held(handle, selected):
        entered.set()
        assert release.wait(5)
        return original(handle, selected)
    monkeypatch.setattr(ParentHandle, 'capture_skills', held)
    operation = asyncio.create_task(session.children.runtime.start('must not start before admission'))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        owner = session.children.runtime
        assert not owner.active and not owner.snapshot()['children'] and not contexts
        assert not session.children.store.root.exists()
        if outcome == 'pause':
            with owner.idle_scope():  # Must not contend with a thread lock held across catalog IO.
                release.set()
                with pytest.raises(RuntimeError, match='changing'):
                    await operation
        elif outcome == 'cancel':
            operation.cancel()
            await asyncio.sleep(0.02)
            assert not operation.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation
        else:
            closing = asyncio.create_task(owner.aclose())
            await wait(lambda: owner.closed)
            await asyncio.sleep(0.02)
            assert not closing.done()
            release.set()
            result = await asyncio.gather(operation, return_exceptions=True)
            assert isinstance(result[0], (ValueError, RuntimeError, asyncio.CancelledError))
            await closing
        assert not owner.snapshot()['children'] and not contexts
        assert not session.children.parents and not session.children.skill_limits and not session.children.snapshots
        assert not session.children.store.root.exists()
    finally:
        release.set()
        await asyncio.gather(operation, return_exceptions=True)
        if closing is not None:
            await closing
        await close(session)


async def test_delegable_skill_metadata_is_readonly_and_live_owner_bound(tmp_path, monkeypatch):
    session, contexts = create(tmp_path)
    install_skills(session, tmp_path)
    try:
        before = session.agent.active_skills
        rows = await asyncio.to_thread(session.children.delegable_skills)
        assert rows == [{'slug': 'private', 'description': 'private description'},
                        {'slug': 'review', 'description': 'review description'}]
        assert all(set(row) == {'slug', 'description'} for row in rows)
        assert not contexts and session.agent.active_skills == before
        assert not session.children.runtime.active
        from xdog.agent.skills.tool import _Reader
        original = _Reader.listing
        def retire_during_read(reader, stopped):
            value = original(reader, stopped)
            session.children._closed = True
            return value
        monkeypatch.setattr(_Reader, 'listing', retire_during_read)
        with pytest.raises(ValueError, match='changed during catalog read'):
            await asyncio.to_thread(session.children.delegable_skills)
        with pytest.raises(ValueError, match='unavailable'):
            await asyncio.to_thread(session.children.delegable_skills)
        assert not contexts
    finally:
        await close(session)


async def test_assistant_tool_preamble_is_received_not_completed_and_tool_result_is_not_reply(tmp_path):
    session, _ = create(tmp_path, 'allow-all')
    events = []
    def stream(model, context, options):
        if context.messages[-1].role == 'user':
            message = AssistantMessage(content=(TextContent(text='Working on your request'),
                ToolCall(id='clock', name='current_time', arguments={})), stop_reason='toolUse')
        else:
            message = AssistantMessage(content=(TextContent(text='Completed findings'),))
        return EventStream.empty(message)
    session.agent.set_stream_fn(stream)
    session.children.runtime.subscribe(events.append)
    try:
        identifier = await session.children.runtime.start('request', access='inherit')
        record = await session.children.runtime.control('wait', identifier)
        assert record['status'] == 'completed', record['error']
        replies = [row for row in events if row['type'] == 'child_message' and row['direction'] == 'child_to_parent']
        assert [(row['text'], row['status']) for row in replies] == [
            ('Working on your request', 'received'), ('Completed findings', 'completed')]
        assert len({row['message_id'] for row in replies}) == 2
    finally:
        await close(session)


@pytest.mark.parametrize('revocation', ['tool', 'permission', 'owner'])
async def test_completed_side_effect_result_survives_parent_revocation_without_replay(tmp_path, revocation):
    session, _ = create(tmp_path, 'allow-all')
    entered, release = threading.Event(), threading.Event()
    effects, requests = [], []
    async def execute(call_id, params, cancel=None, on_update=None, **kwargs):
        effects.append('COMMITTED')
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.002)
        return AgentToolResult.from_text('ACTUAL SIDE EFFECT COMMITTED')
    original = AgentTool(name='effect', parameters={'type': 'object'}, execute=execute)
    session.agent.set_tools((*session.agent.state.tools, original))
    def stream(model, context, options):
        requests.append(context)
        message = (AssistantMessage(content=(ToolCall(id='effect1', name='effect', arguments={}),))
                   if len(requests) == 1 else AssistantMessage(content=(TextContent(text='done'),)))
        return EventStream.empty(message)
    session.agent.set_stream_fn(stream)
    try:
        identifier = await session.children.runtime.start('work', access='inherit', config={'tools': ['effect']})
        assert await asyncio.to_thread(entered.wait, 3)
        if revocation == 'tool':
            session.agent.set_tools(tuple(tool for tool in session.agent.state.tools if tool is not original))
        elif revocation == 'permission':
            session.permissions.set_mode('deny')
        else:
            session.children._closed = True
        release.set()
        record = await session.children.runtime.control('wait', identifier)
        result = next(row for row in record['messages'] if row['role'] == 'toolResult')
        assert result.get('is_error', False) is False and 'ACTUAL SIDE EFFECT COMMITTED' in str(result['content'])
        assert effects == ['COMMITTED']
        if revocation == 'owner':
            assert record['status'] == 'failed' and len(requests) == 1
        else:
            assert record['status'] == 'completed' and len(requests) == 2
        assert session.children.store.load()[0]['messages'] == record['messages']
    finally:
        release.set()
        await close(session)


@pytest.mark.parametrize('revoke_before_call', [False, True])
async def test_child_extension_executor_is_independent_but_stays_bound_to_original_parent_registration(
        tmp_path, revoke_before_call):
    import shutil
    from pathlib import Path
    source = Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/sample'
    root = tmp_path / 'extensions'
    shutil.copytree(source, root / 'sample')
    (tmp_path / 'settings.json').write_text(json.dumps({'extensions': ['sample'], 'extension_dir': str(root)}))
    session, _ = create(tmp_path, 'allow-all')
    entered, release = threading.Event(), threading.Event()
    requests, agents = [], []
    await session.initialize_extensions()
    name = next(tool.name for tool in session.agent.active_tools if tool.name.startswith('ext_sample_'))
    def stream(model, context, options):
        requests.append(context)
        message = (AssistantMessage(content=(ToolCall(id='echo1', name=name, arguments={'value': 'child'}),))
                   if len(requests) == 1 else AssistantMessage(content=(TextContent(text='done'),)))
        completed = asyncio.get_running_loop().create_future()
        completed.set_result(message)
        async def generate():
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.002)
            yield DoneEvent(message=message)
        return EventStream.from_async_generator(generate(), result_future=completed)
    session.agent.set_stream_fn(stream)
    original = session.children.create
    async def factory(spec, publish):
        agent = await original(spec, publish)
        agents.append(agent)
        return agent
    session.children.runtime.factory = factory
    try:
        identifier = await session.children.runtime.start('work', access='inherit', config={'tools': [name]})
        assert await asyncio.to_thread(entered.wait, 3)
        assert session.children.extensions.owners[identifier] is not session.extensions
        assert agents[0].state.tools[0] is not next(tool for tool in session.agent.active_tools if tool.name == name)
        if revoke_before_call:
            await session.extensions.manage('unload', 'sample')
            # Deliberately do not sync parent Agent inventory: stale source identities
            # alone cannot keep an unloaded extension registration delegated.
            assert any(tool.name == name for tool in session.agent.active_tools)
        release.set()
        record = await session.children.runtime.control('wait', identifier)
        assert record['status'] == 'completed', record['error']
        result = next(row for row in record['messages'] if row['role'] == 'toolResult')
        if revoke_before_call:
            assert result['is_error'] and 'EXTENSION-RESULT-child' not in str(result['content'])
            assert not requests[-1].tools
        else:
            assert not result['is_error'] and 'EXTENSION-RESULT-child-POST' in str(result['content'])
        assert not session.agent.messages and len(requests) == 2
    finally:
        release.set()
        await close(session)
