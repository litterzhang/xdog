"""Effect data is inert; only current installed object identities can classify."""
from __future__ import annotations

import asyncio
from copy import copy
from dataclasses import FrozenInstanceError, replace
from itertools import product
from types import SimpleNamespace

import pytest
from xdog.agent.capabilities.runtime import CapabilitySet
from xdog.agent.core import AgentTool
from xdog.agent.tool_effects import ToolEffect

LEGAL = {
    'read': ('workspace', 'outside', 'tool_data'),
    'write': ('workspace', 'outside', 'tool_data', 'external'),
    'execute': ('unknown',),
}


@pytest.mark.parametrize(('access', 'scope', 'approval'), [
    (access, scope, approval) for access, scopes in LEGAL.items()
    for scope, approval in product(scopes, (False, True))])
def test_effect_is_only_frozen_validated_data(access, scope, approval):
    effect = ToolEffect(access, scope, approval)
    assert (effect.access, effect.scope, effect.requires_approval) == (access, scope, approval)
    with pytest.raises(FrozenInstanceError):
        effect.scope = 'external'


@pytest.mark.parametrize(('access', 'scope', 'approval'), [
    (access, scope, approval)
    for access, scope, approval in product(LEGAL, ('workspace', 'outside', 'tool_data', 'external', 'unknown'),
                                         (False, True))
    if scope not in LEGAL[access]])
def test_invalid_access_scope_combinations_fail_closed(access, scope, approval):
    with pytest.raises(ValueError, match='combination'):
        ToolEffect(access, scope, approval)


@pytest.mark.parametrize('fields', [
    {'access': 'delete', 'scope': 'workspace'}, {'access': 'read', 'scope': 'goal'},
    {'access': None, 'scope': 'unknown'}, {'access': 'read', 'scope': []},
    {'access': 'write', 'scope': 'tool_data', 'requires_approval': 1},
    {'access': 'write', 'scope': 'tool_data', 'requires_approval': 'false'},
])
def test_invalid_effect_data_is_rejected(fields):
    with pytest.raises(ValueError):
        ToolEffect(**fields)


class Binding:
    def __init__(self, tool):
        self.tools = (tool,)
        self.calls = []
        self.effect = ToolEffect('write', 'tool_data')
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_release.set()

    def tool_effect(self, tool, args):
        self.calls.append((tool, args))
        return self.effect

    async def aclose(self):
        self.close_entered.set()
        await self.close_release.wait()


def install(monkeypatch, binding, open_binding=None):
    import xdog.agent.capabilities.runtime as runtime

    async def open_default(host):
        return binding

    provider = SimpleNamespace(name='sample', scopes=('session',), tool_names=('sample',),
                               requirements=(), open=open_binding or open_default)
    monkeypatch.setattr(runtime, 'entry_points', lambda **kwargs: (
        SimpleNamespace(name='sample', load=lambda: provider),))
    return CapabilitySet('session')


def definition(converted):
    if not converted:
        return AgentTool('sample')
    return SimpleNamespace(name='sample', description='', parameters={}, execute=None)


@pytest.mark.parametrize('converted', [False, True])
async def test_effect_requires_exact_live_inventory_identity(monkeypatch, converted):
    original = definition(converted)
    binding = Binding(original)
    capabilities = install(monkeypatch, binding)
    assert capabilities.tool_effect(AgentTool('sample'), {}) is None
    assert capabilities._start_task is None and not binding.calls
    await capabilities.start(object())
    try:
        tool, = capabilities.tools
        args = {'action': 'data'}
        assert capabilities.tool_effect(tool, args) is binding.effect
        assert binding.calls == [(original, args)] and binding.calls[0][0] is original
        for forged in (copy(tool), replace(tool), AgentTool('sample')):
            assert capabilities.tool_effect(forged, args) is None
        assert len(binding.calls) == 1
        binding.tools = (definition(converted),)
        assert capabilities.owns_tool(tool)  # Retirement cleanup identity is not authority.
        assert capabilities.tool_effect(tool, args) is None
        successor, = capabilities.tools
        assert successor is not tool
        assert capabilities.tool_effect(successor, args) is binding.effect
        binding.close_release.clear()
        closing = asyncio.create_task(capabilities.aclose())
        await binding.close_entered.wait()
        assert capabilities.tool_effect(successor, args) is None
        binding.close_release.set()
        await closing
        assert capabilities.tool_effect(successor, args) is None
    finally:
        binding.close_release.set()
        await capabilities.aclose()


async def test_converted_definition_cannot_borrow_another_binding(monkeypatch):
    original = definition(True)
    first, second = Binding(original), Binding(original)
    capabilities = install(monkeypatch, first)
    await capabilities.start(object())
    try:
        old, = capabilities.tools
        capabilities._bindings['sample'] = second
        assert capabilities.tool_effect(old, {}) is None
        current, = capabilities.tools
        assert current is not old
        assert capabilities.tool_effect(old, {}) is None
        assert capabilities.tool_effect(current, {}) is second.effect
        assert not first.calls and second.calls == [(original, {})]
    finally:
        await capabilities.aclose()
        await first.aclose()


async def test_starting_and_failed_start_never_classify(monkeypatch):
    binding = Binding(AgentTool('sample'))
    entered, release = asyncio.Event(), asyncio.Event()

    async def opening(host):
        entered.set()
        await release.wait()
        raise ValueError('failed startup')

    capabilities = install(monkeypatch, binding, opening)
    task = asyncio.create_task(capabilities.start(object()))
    await entered.wait()
    assert capabilities.tool_effect(binding.tools[0], {}) is None
    release.set()
    with pytest.raises(ValueError, match='failed startup'):
        await task
    assert capabilities.tool_effect(binding.tools[0], {}) is None
    assert not binding.calls
    await capabilities.aclose()


async def test_callback_is_optional_and_return_type_is_checked(monkeypatch):
    binding = Binding(AgentTool('sample'))
    capabilities = install(monkeypatch, binding)
    await capabilities.start(object())
    try:
        tool, = capabilities.tools
        for invalid in ({'access': 'read', 'scope': 'tool_data'}, 'read', False):
            binding.effect = invalid
            with pytest.raises(TypeError, match='ToolEffect or None'):
                capabilities.tool_effect(tool, {})
        binding.effect = None
        assert capabilities.tool_effect(tool, {}) is None
        binding.tool_effect = None
        assert capabilities.tool_effect(tool, {}) is None
    finally:
        await capabilities.aclose()
