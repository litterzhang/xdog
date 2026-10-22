"""Ordinary children use scoped capability factories through real host policy gates."""
from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from xdog.agent.capabilities.runtime import CapabilitySet
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.agent.tool_effects import ToolEffect
from xdog.ai.types import AssistantMessage, Model, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream

from packages.claw.tests.test_child_runtime import application, prepared
from packages.coding.tests.test_child_agents import create
from packages.coding.tests.test_child_host_boundary import close


class ScopedProvider:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.calls, self.effects, self.validations = [], [], []
        self.valid = True
        self.available_ids = {"a", "b"}
        self.requested_item = None
        self.requires_approval = True
        self.on_validate = None
        async def unscoped(*args, **kwargs):
            raise AssertionError('Parent unscoped executor must not run')
        async def validate(params):
            assert asyncio.get_running_loop() is self.loop
            self.validations.append(dict(params))
        self.tool = AgentTool(name='sample', parameters={
            'type': 'object', 'properties': {'action': {'enum': ['read', 'write', 'parent_control']},
                                            'id': {'type': 'string'}, 'parent_secret': {'type': 'string'}},
            'required': ['action', 'id'], 'additionalProperties': False},
            execute=unscoped, validate_arguments=validate)
        self.tools = (self.tool,)

    async def aclose(self):
        pass

    async def delegate_child_tool(self, actual_tool, spec, scope):
        assert asyncio.get_running_loop() is self.loop and actual_tool is self.tool
        self.calls.append((spec.id, scope))
        if (not self.valid or set(scope) != {'ids'} or not scope['ids']
                or not set(scope['ids']) <= self.available_ids):
            raise ValueError('scope no longer valid')
        async def validate(params):
            assert asyncio.get_running_loop() is self.loop
            if self.on_validate is not None:
                await self.on_validate()
            if not self.valid or params.get('id') not in scope['ids']:
                raise ValueError('arguments exceed scoped data')
        async def execute(call, params, cancel=None, on_update=None, *, ctx=None):
            assert asyncio.get_running_loop() is self.loop and cancel is None
            self.effects.append(dict(params))
            return AgentToolResult.from_text('scoped effect')
        schema = copy.deepcopy(actual_tool.parameters)
        schema['properties'].pop('parent_secret')
        schema['properties']['action']['enum'] = ['read', 'write']
        schema['properties']['id']['enum'] = list(scope['ids'])
        return replace(actual_tool, description='Only scoped task state', parameters=schema,
                       execute=execute, validate_arguments=validate)

    def tool_effect(self, actual_tool, args):
        assert actual_tool is self.tool
        return ToolEffect('read' if args.get('action') == 'read' else 'write', 'tool_data',
                          self.requires_approval if args.get('action') != 'read' else False)


@asynccontextmanager
async def host_owner(tmp_path, monkeypatch, host, *, mode='allow-all', action='read', item='a'):
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
                        lambda scope: ())
    application_scope = host == 'coding_application'
    if application_scope:
        host = 'coding'
    app = None
    if host == 'coding':
        session, contexts = create(tmp_path, mode)
    else:
        app = application(tmp_path, lambda *args: EventStream.empty(AssistantMessage()), tools=())
        session = await prepared(app)
        contexts = []
    binding = ScopedProvider()
    async def open_(host):
        return binding
    provider = SimpleNamespace(name='sample', tool_names=('sample',), requirements=(), open=open_)
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
                        lambda scope: (provider,) if scope == ('application' if application_scope else
                                                             'session' if host == 'coding' else 'group') else ())
    capabilities = CapabilitySet('application' if application_scope else 'session' if host == 'coding' else 'group')
    await capabilities.start(object())
    if application_scope:
        session.application_capabilities = capabilities
    elif host == 'coding':
        session.capabilities = capabilities
    else:
        session._runtime.capabilities = capabilities
    session.agent.set_tools((*[tool for tool in session.agent.state.tools if tool.name != 'sample'], binding.tool))
    def stream(model, context, options):
        contexts.append(context)
        content = ((ToolCall(id=f'effect-{len(contexts)}', name='sample',
                             arguments={'action': action, 'id': binding.requested_item or item}),)
                   if context.messages[-1].role == 'user' else (TextContent(text='finished'),))
        return EventStream.empty(AssistantMessage(content=content))
    model = session.agent.state.model
    class Adapter:
        def resolve_model(self, name):
            return Model(id=name, context_window=128000, max_tokens=4096, supports_tool_calls=True) \
                if name == model else None
        def __call__(self, model, context, options):
            return stream(model, context, options)
    session.agent.set_stream_fn(Adapter())
    try:
        yield session, binding, contexts
    finally:
        await session.children.runtime.aclose()
        await capabilities.aclose()
        if app is not None:
            await app.stop()
        else:
            await close(session)


def profile(**extra):
    return {'tools': ['sample'], 'tool_scopes': {'sample': {'ids': ['a']}}, **extra}


@pytest.mark.parametrize('host', ['coding', 'coding_application', 'claw'])
async def test_explicit_scoped_capability_runs_in_ordinary_child_and_persists_only_data(tmp_path, monkeypatch, host):
    async with host_owner(tmp_path, monkeypatch, host) as (session, binding, contexts):
        identifier = await session.children.runtime.start('work', config=profile())
        result = await session.children.runtime.control('wait', identifier)
        assert result['status'] == 'completed'
        assert binding.effects == [{'action': 'read', 'id': 'a'}]
        assert binding.calls == [(identifier, {'ids': ['a']})]
        assert binding.validations
        assert contexts and [tool.name for tool in contexts[0].tools] == ['sample']
        schema = contexts[0].tools[0].parameters
        assert 'parent_control' not in str(schema) and 'parent_secret' not in str(schema)
        assert schema['properties']['id']['enum'] == ['a']
        record, = session.children.store.load()
        assert record['config']['tool_scopes'] == profile()['tool_scopes']
        assert not session.agent.messages
        assert not session.children.scoped_tools
        # Ordinary implicit selection remains unable to inherit this capability.
        identifier = await session.children.runtime.start('ordinary')
        await session.children.runtime.control('wait', identifier)
        assert all(tool.name != 'sample' for tool in contexts[-2].tools or ())
        assert len(binding.effects) == 1


@pytest.mark.parametrize('host', ['coding', 'coding_application', 'claw'])
@pytest.mark.parametrize('failure', ['missing_scope', 'invalid_scope', 'unsupported', 'unselected', 'forged'])
async def test_scope_failure_precedes_child_catalog_agent_and_model(tmp_path, monkeypatch, host, failure):
    async with host_owner(tmp_path, monkeypatch, host) as (session, binding, contexts):
        config = profile()
        if failure == 'missing_scope':
            config.pop('tool_scopes')
        elif failure == 'invalid_scope':
            config['tool_scopes']['sample']['ids'] = ['other']
        elif failure == 'unsupported':
            binding.delegate_child_tool = None
        elif failure == 'unselected':
            session.agent.set_active_tools([], expected_revision=session.agent.tool_selection_revision)
        else:
            session.agent.set_tools((*[tool for tool in session.agent.state.tools if tool is not binding.tool],
                                     replace(binding.tool)))
        with pytest.raises(ValueError):
            await session.children.runtime.start('must not admit', config=config)
        assert not contexts and not binding.effects
        assert session.children.runtime.snapshot()['children'] == []
        assert session.children.store.load() == []
        assert not session.children.snapshots and not session.children.scoped_tools
        assert not session.children.extensions.owners


@pytest.mark.parametrize('host', ['coding', 'coding_application', 'claw'])
@pytest.mark.parametrize('restriction', ['scope', 'action', 'child_readonly', 'parent_readonly'])
async def test_scoped_tools_do_not_bypass_scope_actions_or_readonly(tmp_path, monkeypatch, host, restriction):
    async with host_owner(tmp_path, monkeypatch, host, action='write',
                          item='outside' if restriction == 'scope' else 'a') as (session, binding, contexts):
        config = profile()
        if restriction == 'action':
            config['tool_actions'] = {'sample': ['read']}
        if restriction == 'parent_readonly':
            session.access.set_read_only(True)
        identifier = await session.children.runtime.start('work', config=config,
            access='read-only' if restriction == 'child_readonly' else 'inherit')
        result = await session.children.runtime.control('wait', identifier)
        assert result['status'] == 'completed'
        assert not binding.effects
        assert 'error' in str(result['messages']).lower() or 'read-only' in str(result['messages'])


@pytest.mark.parametrize('host', ['coding', 'coding_application'])
@pytest.mark.parametrize('mode,access,action,requires_approval,approval,effects', [
    ('deny', 'inherit', 'read', False, None, 1),
    ('deny', 'inherit', 'write', False, None, 0),
    ('ask', 'inherit', 'write', True, None, 0),
    ('ask', 'inherit', 'write', False, None, 1),
    ('ask-all', 'inherit', 'write', False, 'allow_once', 1),
    ('ask', 'inherit', 'write', True, 'allow_once', 1),
    ('ask-all', 'read-only', 'read', False, 'allow_once', 1),
    ('allow-all', 'read-only', 'write', False, None, 0),
])
async def test_coding_scope_uses_existing_approval_and_effect_policy(
        tmp_path, monkeypatch, host, mode, access, action, requires_approval, approval, effects):
    async with host_owner(tmp_path, monkeypatch, host, mode=mode, action=action) as (session, binding, contexts):
        binding.requires_approval = requires_approval
        requests = []
        if approval:
            def approve(identifier, request):
                requests.append(request)
                session.children.resolve(identifier, request.id, approval)
            session.children.approver = approve
        identifier = await session.children.runtime.start('work', access=access, config=profile())
        await session.children.runtime.control('wait', identifier)
        assert len(binding.effects) == effects
        if approval:
            assert requests and requests[0].effect.scope == 'tool_data'
            assert requests[0].effect.access == action


@pytest.mark.parametrize('host', ['coding', 'coding_application', 'claw'])
@pytest.mark.parametrize('revoke', ['readonly', 'identity', 'validator', 'provider_identity', 'scope_owner'])
async def test_scope_executor_rechecks_after_async_validation(tmp_path, monkeypatch, host, revoke):
    async with host_owner(tmp_path, monkeypatch, host, action='write') as (session, binding, contexts):
        validations = []
        async def on_validate():
            validations.append(True)
            if len(validations) == 2:  # effect-time, after preparation/approval
                await asyncio.sleep(0)
                if revoke == 'readonly':
                    session.access.set_read_only(True)
                elif revoke == 'identity':
                    session.agent.set_tools(tuple(replace(tool) if tool is binding.tool else tool
                                                  for tool in session.agent.state.tools))
                elif revoke == 'provider_identity':
                    binding.tools = (replace(binding.tool),)
                elif revoke == 'scope_owner':
                    if host == 'coding_application':
                        session.application_capabilities = CapabilitySet('application')
                    elif host == 'coding':
                        session.capabilities = CapabilitySet('session')
                    else:
                        session._runtime.capabilities = CapabilitySet('group')
                else:
                    binding.valid = False
        binding.on_validate = on_validate
        identifier = await session.children.runtime.start('work', access='inherit', config=profile())
        result = await session.children.runtime.control('wait', identifier)
        assert result['status'] in {'completed', 'failed'} and len(validations) == 2
        assert not binding.effects


@pytest.mark.parametrize('host', ['coding', 'coding_application', 'claw'])
async def test_followup_rebuild_revalidates_scope_and_current_authority(tmp_path, monkeypatch, host):
    async with host_owner(tmp_path, monkeypatch, host) as (session, binding, contexts):
        identifier = await session.children.runtime.start('work', config=profile())
        await session.children.runtime.control('wait', identifier)
        assert len(binding.effects) == len(binding.calls) == 1
        await session.children.runtime.control('follow-up', identifier, 'again')
        result = await session.children.runtime.control('wait', identifier)
        assert result['status'] == 'completed', result['error']
        assert len(binding.effects) == len(binding.calls) == 2
        binding.valid = False
        before = len(contexts)
        record_before, = session.children.store.load()
        with pytest.raises(ValueError, match='scope no longer valid'):
            await session.children.runtime.control('follow-up', identifier, 'invalid scope now')
        result = await session.children.runtime.control('inspect', identifier)
        assert result['status'] == 'completed'
        assert session.children.store.load() == [record_before]
        assert len(binding.effects) == 2 and len(contexts) == before


@pytest.mark.parametrize('host', ['coding', 'coding_application', 'claw'])
async def test_scoped_advertisement_intersects_action_selection_without_parent_controls(tmp_path, monkeypatch, host):
    async with host_owner(tmp_path, monkeypatch, host) as (session, binding, contexts):
        identifier = await session.children.runtime.start('work', config=profile(tool_actions={'sample': ['read']}))
        result = await session.children.runtime.control('wait', identifier)
        assert result['status'] == 'completed'
        schema = contexts[0].tools[0].parameters
        assert schema['properties']['action']['enum'] == ['read']
        assert schema['properties']['id']['enum'] == ['a']
        assert 'write' not in str(schema) and 'parent_control' not in str(schema)
        assert 'parent_secret' not in str(schema)
        assert len(binding.effects) == 1


@pytest.mark.parametrize('host', ['coding', 'coding_application', 'claw'])
@pytest.mark.parametrize('revoke', [None, 'scope', 'parent_tool'])
async def test_restored_child_scopes_are_data_only_and_revalidated_before_input_persistence(
        tmp_path, monkeypatch, host, revoke):
    async with host_owner(tmp_path, monkeypatch, host) as (session, binding, contexts):
        identifier = await session.children.runtime.start('work', config=profile())
        await session.children.runtime.control('wait', identifier)
        await session.children.runtime.aclose()
        if host.startswith('coding'):
            session._load_children()
        else:
            from xdog.claw.core.persistence.session_preparation import children
            from xdog.claw.core.runtime.child_agents import ClawChildren
            restored = await asyncio.to_thread(children, session._store, session.meta.session_id)
            session.children = ClawChildren(session, restored)
        assert len(binding.effects) == len(binding.calls) == 1
        assert session.children.runtime._loop is None
        before = len(contexts)
        record_before, = session.children.store.load()
        if revoke:
            if revoke == 'scope':
                binding.valid = False
            else:
                session.agent.set_active_tools([], expected_revision=session.agent.tool_selection_revision)
            with pytest.raises(ValueError):
                await session.children.runtime.control('follow-up', identifier, 'must not commit')
            assert len(binding.effects) == 1 and len(contexts) == before
            assert session.children.store.load() == [record_before]
            assert not session.children.snapshots and not session.children.scoped_tools
        else:
            await session.children.runtime.control('follow-up', identifier, 'explicit new work')
            result = await session.children.runtime.control('wait', identifier)
            assert result['status'] == 'completed', result['error']
            assert len(binding.effects) == len(binding.calls) == 2


@pytest.mark.parametrize('host', ['coding', 'coding_application'])
@pytest.mark.parametrize('revoke', ['permission', 'access', 'selection', 'effect'])
async def test_scoped_capability_cannot_borrow_authority_changed_during_approval(tmp_path, monkeypatch, host, revoke):
    async with host_owner(tmp_path, monkeypatch, host, mode='ask', action='write') as (session, binding, contexts):
        requests = []
        def approve(identifier, request):
            requests.append(request)
            if revoke == 'permission':
                session.permissions.set_mode('deny')
            elif revoke == 'access':
                session.access.set_read_only(True)
            elif revoke == 'selection':
                session.agent.set_active_tools([], expected_revision=session.agent.tool_selection_revision)
            else:
                # Same argument shape, different actual effect after the review.
                binding.requires_approval = False
            session.children.resolve(identifier, request.id, 'allow_once')
        session.children.approver = approve
        identifier = await session.children.runtime.start('work', access='inherit', config=profile())
        result = await session.children.runtime.control('wait', identifier)
        assert result['status'] in {'completed', 'failed'}
        assert requests and not binding.effects


@pytest.mark.parametrize('host', ['coding', 'coding_application', 'claw'])
async def test_modified_saved_scope_cannot_expand_original_schema_ceiling(tmp_path, monkeypatch, host):
    async with host_owner(tmp_path, monkeypatch, host) as (session, binding, contexts):
        identifier = await session.children.runtime.start('work', config=profile())
        await session.children.runtime.control('wait', identifier)
        record, = session.children.store.load()
        assert record['tool_schemas']['sample']['properties']['id']['enum'] == ['a']
        assert record['tool_schemas']['sample']['properties']['action']['enum'] == ['read', 'write']
        await session.children.runtime.aclose()
        # Scope JSON is selection data, not authority. Even a trusted provider
        # accepting this broader current selection must retain the old ceiling.
        record['config']['tool_scopes']['sample']['ids'].append('b')
        session.children.store.save(record)
        if host.startswith('coding'):
            session._load_children()
        else:
            from xdog.claw.core.persistence.session_preparation import children
            from xdog.claw.core.runtime.child_agents import ClawChildren
            restored = await asyncio.to_thread(children, session._store, session.meta.session_id)
            session.children = ClawChildren(session, restored)
        binding.requested_item = 'b'
        before = len(contexts)
        await session.children.runtime.control('follow-up', identifier, 'attempt broader scope')
        result = await session.children.runtime.control('wait', identifier)
        assert result['status'] == 'completed', result['error']
        assert binding.calls[-1] == (identifier, {'ids': ['a', 'b']})
        assert len(binding.effects) == 1
        assert contexts[before].tools[0].parameters['properties']['id']['enum'] == ['a']
        assert 'b' not in contexts[before].tools[0].parameters['properties']['id']['enum']


@pytest.mark.parametrize('host', ['coding', 'coding_application', 'claw'])
async def test_scope_owner_replacement_during_factory_cannot_commit_child_admission(tmp_path, monkeypatch, host):
    async with host_owner(tmp_path, monkeypatch, host) as (session, binding, contexts):
        original = binding.delegate_child_tool
        async def replace_owner(actual, spec, scope):
            scoped = await original(actual, spec, scope)
            # The tool object remains in the real parent's selection. Even a
            # successor scope using that exact object is not the original owner.
            scope_name = 'application' if host == 'coding_application' else 'session' if host == 'coding' else 'group'
            successor = CapabilitySet(scope_name)
            await successor.start(object())
            if host == 'coding_application':
                session.application_capabilities = successor
            elif host == 'coding':
                session.capabilities = successor
            else:
                session._runtime.capabilities = successor
            return scoped
        monkeypatch.setattr(binding, 'delegate_child_tool', replace_owner)
        try:
            with pytest.raises(ValueError, match='owner retired'):
                await session.children.runtime.start('must not adopt successor scope', config=profile())
            assert not contexts and not binding.effects
            assert not session.children.runtime.snapshot()['children']
            assert not session.children.store.load()
            assert not session.children.snapshots and not session.children.scoped_tools
        finally:
            owner = (session.application_capabilities if host == 'coding_application' else
                     session.capabilities if host == 'coding' else session._runtime.capabilities)
            await owner.aclose()
