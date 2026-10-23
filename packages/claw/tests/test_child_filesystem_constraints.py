"""Real host hooks and protected filesystem effects retain the parent's ceiling."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from xdog.agent import BeforeToolCallContext
from xdog.agent.children import ChildSpec
from xdog.agent.core import AgentContext
from xdog.ai.types import AssistantMessage, Model, ToolCall

_MISSING = object()


def filesystem_call(agent, path, *, action='read', identifier='filesystem-call'):
    args = {'action': action, 'path': str(path)}
    if action == 'write':
        args['content'] = 'must remain confined'
    call = ToolCall(id=identifier, name='filesystem', arguments=args)
    return BeforeToolCallContext(AssistantMessage(content=(call,)), call, dict(args),
                                 AgentContext(tools=list(agent.active_tools)))


def set_roots(agent, roots):
    context = agent.tool_context
    context.pop('fs_confine_to', None)
    if roots is not _MISSING:
        context['fs_confine_to'] = roots
    agent.set_tool_context(context)


async def execute(agent, context, *, execution_context=_MISSING):
    tool = next(tool for tool in agent.active_tools if tool.name == 'filesystem')
    assert tool.execute is not None
    return await tool.execute(context.tool_call.id, context.args,
        ctx=agent.tool_context if execution_context is _MISSING else execution_context)


@pytest.mark.parametrize('profile', ['missing', 'none', 'empty', 'subset'])
@pytest.mark.parametrize('action', ['read', 'write'])
async def test_parent_and_child_real_filesystem_share_the_same_execution_ceiling(tmp_path, profile, action):
    allowed = tmp_path/'allowed'
    allowed.mkdir()
    inside, outside = allowed/'inside', tmp_path/'outside'
    roots = {'missing': _MISSING, 'none': None, 'empty': [], 'subset': [allowed]}[profile]
    async with child_with_roots(tmp_path, roots) as (session, child):
        assert '_private_parent_marker' not in child.tool_context
        assert child.tool_context.get('fs_confine_to') == (None if roots is _MISSING or roots is None
                                                          else tuple(roots))
        for index, path in enumerate((inside, outside)):
            if action == 'read':
                path.write_text('original safe contents')
            denied = profile == 'empty' or profile == 'subset' and path == outside
            for agent in (session.agent, child):
                context = filesystem_call(agent, path, action=action, identifier=f'call-{index}')
                assert await agent._before_tool_call(context) is None
                result = await execute(agent, context)
                assert ('outside this run\'s workspace' in str(result.content)) == denied
                if action == 'write':
                    assert path.exists() == (not denied)
                    if path.exists():
                        path.unlink()


@pytest.mark.parametrize('change', [
    'narrow', 'widen', 'empty', 'restrict-unconfined', 'unconfine', 'empty-to-subset',
])
@pytest.mark.parametrize('stage', ['before-hook', 'before-executor', 'validator-await'])
async def test_parent_context_changes_invalidate_original_filesystem_delegation(tmp_path, change, stage):
    allowed = tmp_path/'allowed'
    allowed.mkdir()
    roots, replacement = {
        'narrow': ([tmp_path], [allowed]),
        'widen': ([allowed], [tmp_path]),
        'empty': ([allowed], []),
        'restrict-unconfined': (None, []),
        'unconfine': ([allowed], None),
        'empty-to-subset': ([], [tmp_path]),
    }[change]
    broadening = change in {'widen', 'unconfine', 'empty-to-subset'}
    target = tmp_path/'outside' if broadening else allowed/'inside'
    async with child_with_roots(tmp_path, roots) as (session, child):
        captured = child.tool_context.get('fs_confine_to')
        context = filesystem_call(child, target, action='write')
        if stage == 'before-hook':
            if isinstance(roots, list) and isinstance(replacement, list):
                roots[:] = replacement  # Shallow context mutation must also revoke.
            else:
                set_roots(session.agent, replacement)
            result = await child._before_tool_call(context)
            assert result is not None and result.block and 'filesystem execution constraints changed' in result.reason
        else:
            assert await child._before_tool_call(context) is None
            if stage == 'before-executor':
                set_roots(session.agent, replacement)
            else:
                source = next(tool for tool in session.agent.active_tools if tool.name == 'filesystem')
                async def validate(params):
                    await asyncio.sleep(0)
                    set_roots(session.agent, replacement)
                source.validate_arguments = validate
            with pytest.raises(ValueError, match='filesystem execution constraints changed'):
                await execute(child, context)
        assert child.tool_context.get('fs_confine_to') == captured
        assert not target.exists()
        # Widening really grants the parent this path, but cannot authorize an old child.
        if broadening:
            parent_call = filesystem_call(session.agent, target, action='write')
            assert await session.agent._before_tool_call(parent_call) is None
            await execute(session.agent, parent_call)
            assert target.exists()


@pytest.mark.parametrize('execution_context', [None, {}, {'fs_confine_to': None}])
async def test_protected_executor_cannot_drop_captured_filesystem_context(tmp_path, execution_context):
    async with child_with_roots(tmp_path, []) as (session, child):
        context = filesystem_call(child, tmp_path/'outside', action='write')
        assert await child._before_tool_call(context) is None
        with pytest.raises(ValueError, match='Child filesystem execution constraints differ'):
            await execute(child, context, execution_context=execution_context)
        assert not (tmp_path/'outside').exists()


async def test_filesystem_confinement_does_not_bypass_child_read_only_gate(tmp_path):
    async with child_with_roots(tmp_path, [tmp_path], access='read-only') as (session, child):
        context = filesystem_call(child, tmp_path/'outside', action='write')
        result = await child._before_tool_call(context)
        assert result is not None and result.block and 'read-only' in result.reason
        with pytest.raises(ValueError):
            await execute(child, context)
        assert not (tmp_path/'outside').exists()


@asynccontextmanager
async def child_with_roots(tmp_path, roots, *, access='inherit'):
    from xdog.ai.utils.event_stream import EventStream

    from packages.claw.tests.test_child_runtime import application, prepared

    app = application(tmp_path, lambda *args: EventStream.empty(AssistantMessage()), tools=('filesystem',))
    session = await prepared(app)
    set_roots(session.agent, roots)
    session.agent.set_tool_context({**session.agent.tool_context, '_private_parent_marker': object()})
    spec = ChildSpec(id='f'*12, task='filesystem ceiling regression', access=access, tools=('filesystem',))
    child = None
    try:
        spec = await session.children.capture(spec)
        child = await session.children.create(spec, lambda event: None)
        yield session, child
    finally:
        if child is not None:
            await child.aclose()
        await session.children.finish(spec)
        await app.stop()


@pytest.mark.parametrize('change', ['same', 'narrow', 'widen'])
async def test_idle_child_continuation_cannot_recapture_a_later_filesystem_grant(tmp_path, change):
    allowed = tmp_path/'allowed'
    allowed.mkdir()
    async with child_with_roots(tmp_path, [allowed]) as (session, child):
        runtime = session.children.runtime
        original_stream = session.agent.stream_fn
        class Adapter:
            def resolve_model(self, name):
                return Model(id=name, context_window=128000, max_tokens=4096, supports_tool_calls=True)

            def __call__(self, *args):
                return original_stream(*args)
        session.agent.set_stream_fn(Adapter())
        identifier = await runtime.start('safe task', access='inherit', config={'tools': ['filesystem']})
        first = await runtime.control('wait', identifier)
        assert first['status']=='completed', first['error']
        assert identifier not in session.children.snapshots  # Original Agent has been retired.
        if change != 'same':
            set_roots(session.agent, [] if change == 'narrow' else [tmp_path])
        await runtime.control('follow-up', identifier, 'another safe task')
        second = await runtime.control('wait', identifier)
        if change == 'same':
            assert second['status']=='completed', second['error']
        else:
            assert second['status']=='failed'
            assert 'Original child filesystem execution constraints changed' in second['error']
        assert not (tmp_path/'outside').exists()
