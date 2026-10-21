"""P1 regressions at the real Claw admission/prepare/execute boundaries."""
from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap

import pytest
from xdog.agent import AgentTool, AgentToolResult, BeforeToolCallContext
from xdog.agent.children import ChildSpec
from xdog.agent.core import AgentContext
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.types import Group

from packages.claw.tests.test_child_runtime import application, prepared


def test_admission_close_never_joins_requester_that_finally_awaits_same_close():
    # Run the exact cycle PoC in a process: a regressed cancellation-resistant
    # close would otherwise strand the pytest loop's own teardown forever.
    script = textwrap.dedent('''
        import asyncio
        import threading
        from xdog.agent.async_work import run_owned
        from xdog.claw.core.runtime.child_runtime import ClawChildRuntime

        async def main():
            entered, release, settled = (threading.Event() for _ in range(3))
            calls, finalized = [], []
            async def capture(spec):
                def io():
                    entered.set()
                    assert release.wait(4)
                    settled.set()
                await run_owned(io)
            async def finalize(spec):
                assert settled.is_set()
                finalized.append(spec.id)
            runtime = ClawChildRuntime(lambda *args: calls.append(args), capture=capture,
                finalize=finalize, owner_loop=asyncio.get_running_loop)
            async def request():
                try:
                    await runtime.start('must not admit a child')
                finally:
                    await runtime.aclose()
            requester = asyncio.create_task(request())
            assert await asyncio.to_thread(entered.wait, 3)
            assert requester not in runtime._admissions
            closing = asyncio.create_task(runtime.aclose())
            await asyncio.sleep(.02)
            assert not closing.done() and not settled.is_set()
            release.set()
            result = await asyncio.gather(requester, closing, return_exceptions=True)
            assert isinstance(result[0], asyncio.CancelledError), result
            assert result[1] is None, result
            assert settled.is_set() and len(finalized) == 1 and not calls
            assert runtime.closed and not runtime.active and not runtime._admissions
            assert runtime.snapshot()['children'] == []
            assert asyncio.get_running_loop().is_running()
            print('ADMISSION-AND-REQUEST-FINALLY-SETTLED')
        asyncio.run(main())
    ''')
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=12)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'ADMISSION-AND-REQUEST-FINALLY-SETTLED' in result.stdout


async def test_original_async_validator_is_rechecked_after_parallel_sibling_revokes(tmp_path):
    allowed, validation, effects, calls = [True], [], [], []
    async def validate(params):
        await asyncio.sleep(0)
        validation.append(allowed[0])
        if not allowed[0]:
            raise ValueError('original source permission revoked')
    async def revoke(*args, **kwargs):
        allowed[0] = False
        return AgentToolResult.from_text('permission revoked by first executor')
    async def execute(*args, **kwargs):
        effects.append('UNAUTHORIZED')
        return AgentToolResult.from_text('must not execute')
    sources = (AgentTool(name='revoke', parameters={'type': 'object'}, execute=revoke),
               AgentTool(name='write', parameters={'type': 'object'}, execute=execute, validate_arguments=validate))
    def stream(model, context, options):
        calls.append(context)
        content = ((ToolCall(id='first', name='revoke', arguments={}),
                    ToolCall(id='second', name='write', arguments={}))
                   if len(calls) == 1 else (TextContent(text='denied'),))
        return EventStream.empty(AssistantMessage(content=content))
    app = application(tmp_path, stream, tools=())
    try:
        session = await prepared(app)
        session.agent.set_tools((*sources, *session.children.runtime.tools()))
        identifier = await session.children.runtime.start('parallel tools', access='inherit',
            config={'tools': ['revoke', 'write']})
        record = await session.children.runtime.control('wait', identifier)
        assert not effects and validation == [True, False]
        assert 'original source permission revoked' in str(record['messages'])
    finally:
        await app.stop()


async def test_policy_rechecked_after_effect_validator_await_returns(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    validation, effects, calls = [], [], []
    async def validate(params):
        validation.append(dict(params))
        if len(validation) == 2:  # Actual executor validation, not preparation.
            entered.set()
            await release.wait()
    async def execute(*args, **kwargs):
        effects.append('UNAUTHORIZED')
        return AgentToolResult.from_text('must not execute')
    source = AgentTool(name='write', parameters={'type': 'object'}, execute=execute, validate_arguments=validate)
    def stream(model, context, options):
        calls.append(context)
        content = ((ToolCall(id='write', name='write', arguments={}),) if len(calls) == 1
                   else (TextContent(text='denied'),))
        return EventStream.empty(AssistantMessage(content=content))
    app = application(tmp_path, stream, tools=())
    try:
        session = await prepared(app)
        session.agent.set_tools((source, *session.children.runtime.tools()))
        identifier = await session.children.runtime.start('write', access='inherit', config={'tools': ['write']})
        async with asyncio.timeout(4):
            await entered.wait()
        session.access.set_read_only(True)
        release.set()
        record = await session.children.runtime.control('wait', identifier)
        assert not effects and len(validation) == 2
        assert 'read-only' in str(record['messages']).lower()
    finally:
        release.set()
        await app.stop()


def extension_runtime(tmp_path, stream):
    root = tmp_path / 'extensions' / 'guarded'
    root.mkdir(parents=True)
    (root / 'extension.yaml').write_text('name: guarded\nentry_point: main\n')
    (root / 'main.py').write_text(textwrap.dedent('''
        from pathlib import Path
        def setup(api):
            async def execute(params, context):
                Path(context['cwd'], 'extension-effect').write_text(params['value'])
                return 'ACTUAL EXTENSION EFFECT'
            api.tool('target', 'Test actual isolated registration',
                {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}, execute)
            async def rewrite(name, args, context):
                if context.get('child_id') and args.get('value') == 'rewrite':
                    return {'value': 'blocked'}
                return args
            api.before_tool(rewrite)
    '''))
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    return GroupRuntime(group=Group(id='main', enabled_tools=()), workspace_dir=workspace,
        data_dir=tmp_path / 'data', model='test/parent', stream_fn=stream,
        extensions=('guarded',), extension_dir=str(root.parent), extension_allow_tools=('guarded',))


@pytest.mark.parametrize('failure', ['parent-after-rewrite', 'child-executor-validator'])
async def test_actual_extension_proxy_preserves_both_parent_and_child_validators(tmp_path, failure):
    alias, calls, parent_validation, child_validation = '', [], [], []
    def stream(model, context, options):
        calls.append(context)
        content = ((ToolCall(id='extension-call', name=alias,
                            arguments={'value': 'rewrite' if failure == 'parent-after-rewrite' else 'safe'}),)
                   if len(calls) == 1 else (TextContent(text='denied'),))
        return EventStream.empty(AssistantMessage(content=content))
    runtime = extension_runtime(tmp_path, stream)
    session = await runtime.prepare_session(allow_reset=False)
    try:
        await session.initialize_extensions()
        parent_source = next(tool for tool in session.agent.active_tools if tool.name in session.extensions.aliases)
        alias = parent_source.name
        original_parent_validate = parent_source.validate_arguments
        async def parent_validate(params):
            parent_validation.append(dict(params))
            if original_parent_validate is not None:
                await original_parent_validate(params)
            if params['value'] == 'blocked':
                raise ValueError('parent validator denied rewritten arguments')
        parent_source.validate_arguments = parent_validate
        original_start = session.children.extensions.start
        async def start(spec, extension):
            await original_start(spec, extension)
            child_source = next(tool for tool in extension.tools() if tool.name == alias)
            original_child_validate = child_source.validate_arguments
            async def child_validate(params):
                child_validation.append(dict(params))
                if original_child_validate is not None:
                    await original_child_validate(params)
                if failure == 'child-executor-validator' and len(child_validation) > 1:
                    raise ValueError('child registration validator denied effect')
            child_source.validate_arguments = child_validate
        session.children.extensions.start = start
        identifier = await session.children.runtime.start('use extension', access='inherit',
            config={'tools': [alias]})
        record = await session.children.runtime.control('wait', identifier)
        assert record['status'] == 'completed', record['error']
        assert not (runtime.workspace_dir / 'extension-effect').exists()
        assert len(parent_validation) == 2
        if failure == 'parent-after-rewrite':
            assert parent_validation == [{'value': 'rewrite'}, {'value': 'blocked'}]
            assert 'parent validator denied rewritten arguments' in str(record['messages'])
        else:
            assert len(child_validation) == 2
            assert 'child registration validator denied effect' in str(record['messages'])
    finally:
        await runtime.aclose()


async def build_manual_child(tmp_path, effects, *, entered=None, release=None):
    async def execute(call_id, params, *args, **kwargs):
        effects.append((call_id, dict(params)))
        if entered is not None:
            entered.set()
            await release.wait()
        return AgentToolResult.from_text('actual authorized execution')
    source = AgentTool(name='alpha', parameters={'type': 'object', 'properties': {'value': {'type': 'string'}}},
                       execute=execute)
    other = AgentTool(name='beta', parameters=source.parameters, execute=execute)
    app = application(tmp_path, lambda *args: EventStream.empty(AssistantMessage()), tools=())
    session = await prepared(app)
    session.agent.set_tools((source, other, *session.children.runtime.tools()))
    spec = ChildSpec(id='a' * 12, task='manual prepare boundary', access='inherit', tools=('alpha', 'beta'))
    await session.children.capture(spec)
    child = await session.children.create(spec, lambda event: None)
    return app, session, spec, child


def call_context(child, name, value, identifier='original-call'):
    call = ToolCall(id=identifier, name=name, arguments={'value': value})
    assistant = AssistantMessage(content=(call,))
    return BeforeToolCallContext(assistant, call, dict(call.arguments), AgentContext(tools=list(child.state.tools)))


@pytest.mark.parametrize('different', ['tool', 'arguments'])
async def test_host_admission_cannot_be_overwritten_or_borrowed_by_same_id(tmp_path, different):
    effects = []
    app, session, spec, child = await build_manual_child(tmp_path, effects)
    try:
        original = call_context(child, 'alpha', 'original')
        collision = call_context(child, 'beta' if different == 'tool' else 'alpha', 'collision')
        assert await child._before_tool_call(original) is None
        denied = await child._before_tool_call(collision)
        assert denied is not None and denied.block and 'Duplicate' in denied.reason
        proxies = {tool.name: tool for tool in child.state.tools}
        with pytest.raises(ValueError, match='original call/arguments'):
            await proxies[collision.tool_call.name].execute(collision.tool_call.id, collision.args)
        assert not effects
        await proxies['alpha'].execute(original.tool_call.id, original.args)
        assert effects == [('original-call', {'value': 'original'})]
        with pytest.raises(ValueError, match='no original tool admission'):
            await proxies['alpha'].execute(original.tool_call.id, original.args)
    finally:
        await child.aclose()
        await session.children.finish(spec)
        await app.stop()


async def test_host_rejects_post_admission_argument_mutation_before_any_effect(tmp_path):
    effects = []
    app, session, spec, child = await build_manual_child(tmp_path, effects)
    try:
        original = call_context(child, 'alpha', 'admitted')
        assert await child._before_tool_call(original) is None
        original.args['value'] = 'mutated after admission'
        proxy = next(tool for tool in child.state.tools if tool.name == 'alpha')
        with pytest.raises(ValueError, match='original call/arguments'):
            await proxy.execute(original.tool_call.id, original.args)
        assert not effects
        retry = call_context(child, 'alpha', 'freshly admitted')
        assert await child._before_tool_call(retry) is None
        await proxy.execute(retry.tool_call.id, retry.args)
        assert effects == [('original-call', {'value': 'freshly admitted'})]
    finally:
        await child.aclose()
        await session.children.finish(spec)
        await app.stop()


async def test_same_admission_cannot_start_two_concurrent_executors(tmp_path):
    effects = []
    entered, release = asyncio.Event(), asyncio.Event()
    app, session, spec, child = await build_manual_child(tmp_path, effects, entered=entered, release=release)
    running = None
    try:
        original = call_context(child, 'alpha', 'once')
        assert await child._before_tool_call(original) is None
        proxy = next(tool for tool in child.state.tools if tool.name == 'alpha')
        running = asyncio.create_task(proxy.execute(original.tool_call.id, original.args))
        async with asyncio.timeout(3):
            await entered.wait()
        with pytest.raises(ValueError, match='already owns an executor'):
            await proxy.execute(original.tool_call.id, original.args)
        assert not running.done()
        release.set()
        await running
        assert effects == [('original-call', {'value': 'once'})]
    finally:
        release.set()
        if running is not None:
            await asyncio.gather(running, return_exceptions=True)
        await child.aclose()
        await session.children.finish(spec)
        await app.stop()
