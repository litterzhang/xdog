"""Concrete Coding permission/effect PoCs, independent of the SDK duplicate-ID guard."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from xdog.agent import BeforeToolCallContext
from xdog.agent.access import AccessScope
from xdog.agent.core import AgentContext
from xdog.agent.supervision import ChildSpec
from xdog.agent.types import AfterToolCallContext
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream

from packages.coding.tests.test_child_agents import create
from packages.coding.tests.test_child_host_boundary import close


@asynccontextmanager
async def real_child(session):
    # Exercise real host factory/hooks/filesystem executors directly so the SDK's
    # earlier duplicate-ID rejection cannot hide a broken host effect fence.
    spec = ChildSpec(id='0123456789ab', task='permission binding PoC', access='inherit')
    await session.children.capture(spec)
    child = await session.children.create(spec, lambda event: None)
    try:
        yield child
    finally:
        await child.aclose()
        await session.children.finish(spec)


async def prepare(child, call, *, assistant=None, params=None):
    message = assistant or AssistantMessage(content=(call,))
    args = dict(call.arguments) if params is None else params
    ctx = BeforeToolCallContext(message, call, args, AgentContext(tools=list(child.state.tools)))
    assert child._before_tool_call is not None
    result = await child._before_tool_call(ctx)
    assert result is None, result
    return ctx


async def test_same_id_read_cannot_replace_approved_write_epoch_at_actual_filesystem_executor(tmp_path):
    session, _ = create(tmp_path)
    session.children.approver = lambda identifier, request: session.children.resolve(
        identifier, request.id, 'allow_once')
    try:
        async with real_child(session) as child:
            write = ToolCall(id='same', name='filesystem', arguments={
                'action': 'write', 'path': 'must-not-exist', 'content': 'forbidden'})
            read = ToolCall(id='same', name='filesystem', arguments={'action': 'read', 'path': 'safe'})
            message = AssistantMessage(content=(write, read))
            write_ctx = await prepare(child, write, assistant=message)
            session.permissions.set_mode('deny')
            (tmp_path / 'safe').write_text('SAFE READ')
            read_ctx = await prepare(child, read, assistant=message)
            tool = next(tool for tool in child.state.tools if tool.name == 'filesystem')
            rejected = False
            try:
                await tool.execute('same', write_ctx.args, ctx=child.tool_context)
            except ValueError:
                rejected = True
            assert not (tmp_path / 'must-not-exist').exists()
            assert rejected
            # Finalizing the refused write must not erase another original call
            # merely because its correlation ID is the same.
            assert child._after_tool_call is not None
            from xdog.agent import AgentToolResult
            await child._after_tool_call(AfterToolCallContext(message, write, write_ctx.args,
                AgentToolResult.from_text('refused', is_error=True), True, write_ctx.context))
            result = await tool.execute('same', read_ctx.args, ctx=child.tool_context)
            assert not result.is_error and 'SAFE READ' in str(result.content)
    finally:
        await close(session)


@pytest.mark.parametrize('change', ['args', 'permission'])
async def test_actual_effect_validator_cannot_rewrite_approved_read_into_write_or_borrow_new_epoch(tmp_path, change):
    session, _ = create(tmp_path, 'deny' if change == 'args' else 'allow-all')
    validations = []
    source = next(tool for tool in session.agent.state.tools if tool.name == 'filesystem')
    async def validate(params):
        validations.append(dict(params))
        if change == 'args':
            params.update(action='write', path='must-not-exist', content='forbidden')
        else:
            session.permissions.set_mode('deny')
            session.permissions.set_mode('allow-all')
    source.validate_arguments = validate
    try:
        async with real_child(session) as child:
            call = ToolCall(id='read', name='filesystem', arguments={'action': 'read', 'path': 'safe'})
            prepared = await prepare(child, call)
            tool = next(tool for tool in child.state.tools if tool.name == 'filesystem')
            with pytest.raises(ValueError):
                await tool.execute(call.id, prepared.args, ctx=child.tool_context)
            assert len(validations) == 1  # The real effect wrapper retained/re-ran the original validator.
            assert not (tmp_path / 'must-not-exist').exists()
    finally:
        await close(session)


@pytest.mark.parametrize('replacement', ['params_copy', 'other_tool', 'call_arguments'])
async def test_actual_executor_refuses_same_id_with_replaced_call_identity_or_parameters(tmp_path, replacement):
    session, _ = create(tmp_path, 'allow-all')
    try:
        async with real_child(session) as child:
            call = ToolCall(id='same', name='filesystem', arguments={
                'action': 'write', 'path': 'must-not-exist', 'content': 'forbidden'})
            prepared = await prepare(child, call)
            tool = next(tool for tool in child.state.tools if tool.name == 'filesystem')
            params = prepared.args
            if replacement == 'params_copy':
                params = dict(params)
            elif replacement == 'other_tool':
                tool = next(tool for tool in child.state.tools if tool.name == 'current_time')
            else:
                call.arguments['path'] = 'different-original-call'
            with pytest.raises(ValueError):
                await tool.execute(call.id, params, ctx=child.tool_context)
            assert not (tmp_path / 'must-not-exist').exists()
            assert not (tmp_path / 'different-original-call').exists()
    finally:
        await close(session)


async def test_real_model_duplicate_id_write_read_batch_never_writes_after_parent_deny(tmp_path):
    session, _ = create(tmp_path)
    session.children.approver = lambda identifier, request: session.children.resolve(
        identifier, request.id, 'allow_once')
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(
            ToolCall(id='same', name='filesystem', arguments={
                'action': 'write', 'path': 'must-not-exist', 'content': 'forbidden'}),
            ToolCall(id='same', name='filesystem', arguments={'action': 'read', 'path': 'safe'})))
            if len(calls) == 1 else AssistantMessage(content=(TextContent(text='done'),)))
    session.agent.set_stream_fn(stream)
    original = session.children.create
    async def factory(spec, publish):
        child = await original(spec, publish)
        before = child._before_tool_call
        async def revoke_after_write(ctx, cancel=None):
            result = await before(ctx, cancel)
            if result is None and ctx.args.get('action') == 'write':
                session.permissions.set_mode('deny')
            return result
        child.set_before_tool_call(revoke_after_write)
        return child
    session.children.supervisor.factory = factory
    try:
        identifier = await session.children.supervisor.start('model batch PoC', access='inherit')
        async with asyncio.timeout(5):
            record = await session.children.supervisor.control('wait', identifier)
        assert not (tmp_path / 'must-not-exist').exists()
        assert record['status'] == 'failed' and 'duplicate' in str(record['messages']).lower()
        assert not any(row['role'] == 'toolResult' for row in record['messages'])
        assert not any(part.get('type') == 'toolCall' for row in record['messages']
                       if row['role'] == 'assistant' for part in row['content'])
        assert session.permissions.mode == 'ask'  # No prepare hook ran, including the test's revoke hook.
    finally:
        await close(session)


@pytest.mark.parametrize('change', ['selection_cycle', 'tool_remove_restore', 'readonly_cycle', 'access_owner'])
async def test_old_effect_cannot_cross_authority_cycle_but_fresh_preparation_can_proceed(tmp_path, change):
    session, _ = create(tmp_path, 'allow-all')
    original_access = session.access
    try:
        async with real_child(session) as child:
            call = ToolCall(id='old', name='filesystem', arguments={
                'action': 'write', 'path': 'old-effect', 'content': 'old'})
            prepared = await prepare(child, call)
            tool = next(tool for tool in child.state.tools if tool.name == 'filesystem')
            if change == 'selection_cycle':
                session.agent.set_active_tools([], expected_revision=session.agent.tool_selection_revision)
                session.agent.set_active_tools(None, expected_revision=session.agent.tool_selection_revision)
            elif change == 'tool_remove_restore':
                originals = session.agent.state.tools
                session.agent.set_tools(tuple(source for source in originals if source.name != 'filesystem'))
                session.agent.set_tools(originals)
            elif change == 'readonly_cycle':
                session.access.set_read_only(True)
                session.access.set_read_only(False)
            else:
                session.access = AccessScope(session.access.state, lambda value: None)
            with pytest.raises(ValueError, match='changed before child tool execution'):
                await tool.execute(call.id, prepared.args, ctx=child.tool_context)
            assert not (tmp_path / 'old-effect').exists()
            fresh = ToolCall(id='new', name='filesystem', arguments={
                'action': 'write', 'path': 'new-effect', 'content': 'fresh preparation'})
            new_prepared = await prepare(child, fresh)
            result = await tool.execute(fresh.id, new_prepared.args, ctx=child.tool_context)
            assert not result.is_error and (tmp_path / 'new-effect').read_text() == 'fresh preparation'
    finally:
        if session.access is not original_access:
            session.access.close()
            session.access = original_access
        await close(session)


async def test_approval_is_bound_to_displayed_arguments_before_wait_not_mutated_arguments_after_wait(tmp_path):
    session, _ = create(tmp_path)
    requests = []
    session.children.approver = lambda identifier, request: requests.append(request)
    try:
        async with real_child(session) as child:
            call = ToolCall(id='write', name='filesystem', arguments={
                'action': 'write', 'path': 'approved-target', 'content': 'approved material'})
            params = dict(call.arguments)
            message = AssistantMessage(content=(call,))
            ctx = BeforeToolCallContext(message, call, params, AgentContext(tools=list(child.state.tools)))
            pending = asyncio.create_task(child._before_tool_call(ctx))
            async with asyncio.timeout(5):
                while not requests:
                    await asyncio.sleep(0.002)
            params['path'] = 'unapproved-target'
            assert session.children.resolve('0123456789ab', requests[0].id, 'allow_once')
            decision = await pending
            assert decision is not None and decision.block and 'arguments changed' in decision.reason
            tool = next(tool for tool in child.state.tools if tool.name == 'filesystem')
            with pytest.raises(ValueError):
                await tool.execute(call.id, params, ctx=child.tool_context)
            assert not (tmp_path / 'approved-target').exists() and not (tmp_path / 'unapproved-target').exists()
    finally:
        await close(session)


async def test_permission_admission_is_consumed_once_even_before_after_hook_cleanup(tmp_path):
    session, _ = create(tmp_path, 'allow-all')
    try:
        async with real_child(session) as child:
            call = ToolCall(id='write', name='filesystem', arguments={
                'action': 'write', 'path': 'once', 'content': 'first invocation'})
            prepared = await prepare(child, call)
            tool = next(tool for tool in child.state.tools if tool.name == 'filesystem')
            first = await tool.execute(call.id, prepared.args, ctx=child.tool_context)
            assert not first.is_error
            (tmp_path / 'once').write_text('later independent work')
            with pytest.raises(ValueError, match='already consumed'):
                await tool.execute(call.id, prepared.args, ctx=child.tool_context)
            assert (tmp_path / 'once').read_text() == 'later independent work'
    finally:
        await close(session)


@pytest.mark.parametrize('different_executor', [False, True])
async def test_new_call_object_cannot_replace_existing_admission_even_when_it_reuses_params_and_id(
        tmp_path, different_executor):
    session, _ = create(tmp_path, 'allow-all')
    try:
        async with real_child(session) as child:
            call = ToolCall(id='same', name='filesystem', arguments={
                'action': 'write', 'path': 'original', 'content': 'original approval'})
            original = await prepare(child, call)
            impostor = ToolCall(id=call.id, name='current_time' if different_executor else call.name,
                                arguments=dict(call.arguments))
            ctx = BeforeToolCallContext(AssistantMessage(content=(impostor,)), impostor, original.args,
                                       AgentContext(tools=list(child.state.tools)))
            refusal = await child._before_tool_call(ctx)
            assert refusal is not None and refusal.block and 'already has a permission admission' in refusal.reason
            tool = next(tool for tool in child.state.tools if tool.name == 'filesystem')
            result = await tool.execute(call.id, original.args, ctx=child.tool_context)
            assert not result.is_error and (tmp_path / 'original').read_text() == 'original approval'
    finally:
        await close(session)


@pytest.mark.parametrize('cycle', ['permission', 'selection', 'readonly', 'unchanged'])
async def test_child_session_approval_cache_does_not_survive_parent_revoke_restore_cycle(tmp_path, cycle):
    session, _ = create(tmp_path)
    requests = []
    def approve(identifier, request):
        requests.append(request)
        assert session.children.resolve(identifier, request.id, 'allow_session')
    session.children.approver = approve
    try:
        async with real_child(session) as child:
            tool = next(tool for tool in child.state.tools if tool.name == 'filesystem')
            first = ToolCall(id='same', name='filesystem', arguments={
                'action': 'write', 'path': 'approved', 'content': 'approved content'})
            prepared = await prepare(child, first)
            result = await tool.execute(first.id, prepared.args, ctx=child.tool_context)
            assert not result.is_error
            await child._after_tool_call(AfterToolCallContext(prepared.assistant_message, first, prepared.args,
                result, False, prepared.context))
            assert len(requests) == 1
            if cycle == 'permission':
                session.permissions.set_mode('deny')
                session.permissions.set_mode('ask')
            elif cycle == 'selection':
                session.agent.set_active_tools([], expected_revision=session.agent.tool_selection_revision)
                session.agent.set_active_tools(None, expected_revision=session.agent.tool_selection_revision)
            elif cycle == 'readonly':
                session.access.set_read_only(True)
                session.access.set_read_only(False)
            fresh = ToolCall(id=first.id, name=first.name, arguments=dict(first.arguments))
            new_prepared = await prepare(child, fresh)
            assert len(requests) == (1 if cycle == 'unchanged' else 2)
            second = await tool.execute(fresh.id, new_prepared.args, ctx=child.tool_context)
            assert not second.is_error
            assert (tmp_path / 'approved').read_text() == 'approved content'
    finally:
        await close(session)


async def test_skill_picker_respects_host_narrowed_standard_manager_instance_catalog(tmp_path):
    from packages.coding.tests.test_child_host_boundary import install_skills
    session, contexts = create(tmp_path)
    manager, _ = install_skills(session, tmp_path)
    available = manager.list_skills()
    manager.list_skills = lambda: [row for row in available if row.slug == 'review']
    try:
        assert await asyncio.to_thread(session.children.delegable_skills) == [
            {'slug': 'review', 'description': 'review description'}]
        assert not contexts and not session.children.supervisor.active
    finally:
        await close(session)
