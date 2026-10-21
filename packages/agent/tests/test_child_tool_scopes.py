"""Finite data-only scope configuration, and exact provider-owner delegation."""
from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from xdog.agent.capabilities.runtime import CapabilitySet
from xdog.agent.child_config import ChildConfig, ChildPresets
from xdog.agent.children import ChildSpec
from xdog.agent.core import AgentTool, AgentToolResult


@pytest.mark.parametrize('value', [
    {'tool_scopes': {'sample': {}}},
    {'tools': [], 'tool_scopes': {'sample': {}}},
    {'tools': ['sample'], 'tool_scopes': {'sample': []}},
    {'tools': ['sample'], 'tool_scopes': {'sample': {'number': float('nan')}}},
    {'tools': ['sample'], 'tool_scopes': {'sample': {'number': float('inf')}}},
    {'tools': ['sample'], 'tool_scopes': {'sample': {'tuple': (1,)}}},
    {'tools': ['sample'], 'tool_scopes': {'sample': {1: 'non-JSON'}}},
    {'tools': ['sample'], 'tool_scopes': {'sample': {'blob': 'x' * 65536}}},
    {'tools': ['sample'], 'tool_scopes': {'sample': {'items': [0] * 4096}}},
])
def test_scope_config_rejects_non_json_unselected_or_unbounded_data(value):
    with pytest.raises(ValueError):
        ChildConfig.from_dict(value)


def test_scopes_are_detached_roundtrip_and_presets_remain_data_only():
    value = {'tools': ['sample'], 'tool_scopes': {'sample': {'ids': ['a'], 'nested': {'flag': True}}}}
    config = ChildConfig.from_dict(value)
    value['tool_scopes']['sample']['ids'].append('b')
    assert config.tool_scopes['sample']['ids'] == ['a']
    exported = config.to_dict()
    exported['tool_scopes']['sample']['ids'].append('c')
    assert config.tool_scopes['sample']['ids'] == ['a']
    spec = ChildSpec('id', 'work', tools=config.tools, tool_scopes=config.tool_scopes)
    assert spec.config().to_dict() == config.to_dict()
    assert ChildConfig.from_record(config.to_dict()) == config
    presets = ChildPresets({'scoped': {'config': config.to_dict()}})
    assert presets.resolve({'preset': 'scoped'}).tool_scopes == config.tool_scopes
    nested = {}
    for _ in range(18):
        nested = {'next': nested}
    with pytest.raises(ValueError, match='structural'):
        ChildConfig.from_dict({'tools': ['sample'], 'tool_scopes': {'sample': nested}})


async def provider(monkeypatch, *, legacy=False):
    owner = asyncio.get_running_loop()
    calls, validations = [], []
    async def execute(*args, **kwargs):
        raise AssertionError('Unscoped parent executor must never run')
    actual = AgentTool(name='sample', parameters={
        'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id'],
        'additionalProperties': False}, execute=execute)
    definition = SimpleNamespace(name=actual.name, description='', parameters=actual.parameters,
                                 execute=execute) if legacy else actual
    class Binding:
        tools = (definition,)
        async def aclose(self):
            pass
        async def delegate_child_tool(self, tool, spec, scope):
            assert asyncio.get_running_loop() is owner
            calls.append((tool, spec.id, scope))
            if scope.get('ids') != ['a']:
                raise ValueError('invalid domain scope')
            async def validate(params):
                assert asyncio.get_running_loop() is owner
                validations.append(dict(params))
                if params.get('id') not in scope['ids']:
                    raise ValueError('outside domain scope')
            async def scoped(call, params, cancel=None, on_update=None, *, ctx=None):
                assert asyncio.get_running_loop() is owner
                assert cancel is None
                if on_update:
                    await on_update(AgentToolResult.from_text('progress'))
                return AgentToolResult.from_text('scoped result')
            return replace(tool, execute=scoped, validate_arguments=validate)
    binding = Binding()
    async def open_(host):
        return binding
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: (
        SimpleNamespace(name='sample', tool_names=('sample',), open=open_),))
    capabilities = CapabilitySet('session')
    assert not capabilities.can_delegate_child_tool(actual)
    await capabilities.start(object())
    return capabilities, binding, capabilities.tools[0], calls, validations


@pytest.mark.parametrize('legacy', [False, True])
async def test_exact_live_identity_scope_owner_execution_and_retirement(monkeypatch, legacy):
    capabilities, binding, actual, calls, validations = await provider(monkeypatch, legacy=legacy)
    spec = ChildSpec('child', 'work', tools=('sample',), tool_scopes={'sample': {'ids': ['a']}})
    try:
        assert capabilities.can_delegate_child_tool(actual)
        assert not capabilities.can_delegate_child_tool(replace(actual))
        with pytest.raises(ValueError, match='support'):
            await capabilities.delegate_child_tool(replace(actual), spec, {'ids': ['a']})
        with pytest.raises(ValueError, match='invalid domain scope'):
            await capabilities.delegate_child_tool(actual, spec, {'ids': []})
        scope = {'ids': ['a']}
        scoped = await capabilities.delegate_child_tool(actual, spec, scope)
        scope['ids'].append('b')
        assert scoped.name == actual.name and scoped.parameters == actual.parameters
        assert scoped.parameters is not actual.parameters
        with pytest.raises(ValueError, match='outside domain scope'):
            await scoped.validate_arguments({'id': 'b'})
        await scoped.validate_arguments({'id': 'a'})
        updates = []
        async def update(result):
            updates.append(result)
        result = await scoped.execute('call', {'id': 'a'}, on_update=update)
        assert 'scoped result' in str(result) and len(updates) == 1
        with pytest.raises(ValueError, match='schema'):
            await scoped.execute('call', {'id': 'a', 'extra': True})
        binding.tools = (replace(actual),)
        assert not capabilities.can_delegate_child_tool(actual)
        assert capabilities.owns_tool(actual)  # historical ownership is NOT authority
        with pytest.raises(ValueError, match='retired'):
            await scoped.execute('call', {'id': 'a'})
        assert calls[-1][0] is actual
    finally:
        await capabilities.aclose()
    assert not capabilities.can_delegate_child_tool(actual)


async def test_child_loop_factory_validator_executor_and_updates_use_original_loops(monkeypatch):
    capabilities, binding, actual, calls, validations = await provider(monkeypatch)
    owner = asyncio.get_running_loop()
    def worker():
        async def main():
            loop = asyncio.get_running_loop()
            spec = ChildSpec('child', 'work', tools=('sample',))
            scoped = await capabilities.delegate_child_tool(actual, spec, {'ids': ['a']})
            await scoped.validate_arguments({'id': 'a'})
            async def update(result):
                assert asyncio.get_running_loop() is loop and loop is not owner
            await scoped.execute('call', {'id': 'a'}, on_update=update)
        asyncio.run(main())
    try:
        await asyncio.to_thread(worker)
        assert calls and validations
    finally:
        await capabilities.aclose()


async def test_cross_loop_cancel_joins_actual_scoped_executor(monkeypatch):
    capabilities, binding, actual, _, _ = await provider(monkeypatch)
    entered, settled = threading.Event(), threading.Event()
    async def factory(tool, spec, scope):
        async def execute(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                settled.set()
        return replace(tool, execute=execute)
    binding.delegate_child_tool = factory
    spec = ChildSpec('child', 'work', tools=('sample',))
    scoped = await capabilities.delegate_child_tool(actual, spec, {})
    def worker():
        async def main():
            task = asyncio.create_task(scoped.execute('call', {'id': 'a'}))
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert settled.is_set()
        asyncio.run(main())
    try:
        await asyncio.to_thread(worker)
    finally:
        await capabilities.aclose()


def test_scoped_schema_intersection_pins_literals_fields_actions_and_additional_properties():
    from xdog.agent.child_policy import ChildDelegation, _matches
    parent = AgentTool(name='sample', parameters={
        'type': 'object', 'required': ['action', 'id'], 'additionalProperties': False,
        'properties': {'action': {'enum': ['read', 'write', 'admin']}, 'id': {'type': 'string'},
                       'secret': {'type': 'string'}}})
    grant = ChildDelegation.capture((parent,), ('sample',), (('sample', ('read',)),))
    scoped = {'type': 'object', 'required': ['action', 'id'], 'additionalProperties': False,
              'properties': {'action': {'enum': ['read', 'write']}, 'id': {'enum': ['a']}}}
    advertised = grant.scoped_parameters('sample', scoped)
    assert advertised['properties']['action']['enum'] == ['read']
    assert advertised['properties']['id']['enum'] == ['a']
    assert set(advertised['properties']) == {'action', 'id'}
    assert 'admin' not in str(advertised) and 'secret' not in str(advertised)
    assert _matches(advertised, {'action': 'read', 'id': 'a'})
    assert not _matches(advertised, {'action': 'read', 'id': 'b'})
    assert not _matches(advertised, {'action': 'write', 'id': 'a'})
    parent.parameters['properties']['id'] = {'enum': ['b']}
    # Provider advertisement remains the original admission ceiling, while the
    # host's live parent-schema check separately enforces this new narrowing.
    assert grant.scoped_parameters('sample', scoped) == advertised


def test_scoped_intersection_refuses_ambiguous_references_and_filters_cross_properties():
    from xdog.agent.child_policy import _matches, _schema_intersection
    with pytest.raises(ValueError, match='inline'):
        _schema_intersection({'type': 'object'}, {'$ref': '#/$defs/request', '$defs': {'request': {}}})
    schema = _schema_intersection(
        {'type': 'object', 'properties': {'named': {'type': 'string'}}, 'additionalProperties': False},
        {'type': 'object', 'additionalProperties': {'type': 'integer'}})
    assert not _matches(schema, {'named': 'not an integer'})
    assert not _matches(schema, {'named': 1})
    schema = _schema_intersection(
        {'type': 'object', 'additionalProperties': {'type': 'integer'}},
        {'type': 'object', 'properties': {'named': {'type': 'string'}}, 'additionalProperties': False})
    assert not _matches(schema, {'named': 'not an integer'})
    assert not _matches(schema, {'named': 1})


def test_legacy_enum_normalization_never_becomes_scoped_authority():
    from xdog.agent.child_policy import ChildDelegation
    tool = AgentTool(name='sample', parameters={'type': 'object', 'enum': [
        {'action': 'read', 'id': 'a'}, {'action': 'write', 'id': 'b'}]})
    grant = ChildDelegation.capture((tool,), ('sample',))
    with pytest.raises(ValueError, match='legacy object enums'):
        grant.scoped_parameters('sample', {'type': 'object', 'properties': {
            'action': {'enum': ['read']}, 'id': {'enum': ['b']}}})
