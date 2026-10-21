"""Owner-bound SDK capability bridges recheck live authority at actual invocation."""
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from xdog.agent import AgentTool, AgentToolResult
from xdog.agent.capabilities.children import ChildPort
from xdog.agent.supervision import ChildCapture, ChildSpec


async def prepared_bridge(source, live):
    port = ChildPort('session', 'head', lambda: (), parent_valid=lambda identifier: live[0])
    class Backend:
        closed = False
        state = ChildCapture('a'*12, 'task', 'read-only', 'running', 0, 0, 0, True, '', ())
        async def start(self, prompt, **kwargs):
            spec = ChildSpec(self.state.id, prompt)
            port.capture(spec, (), ())
            self.tools = await port.tools(spec)
            return spec.id
        def capture_source(self, identifier):
            assert identifier == self.state.id
            return self.state
    backend = Backend()
    port.supervisor = backend
    async def tools(spec):
        return (source,)
    await port.start('task', access='read-only', config={}, narrow=lambda selected: selected,
                     tools=tools, admitted=lambda spec: None)
    return port, backend, backend.tools[0]


@pytest.mark.parametrize('reason', ['parent', 'cancel', 'settled', 'binding', 'closed'])
async def test_bridge_rejects_retired_or_cancelled_prepared_capability_before_effect(reason):
    effects = []
    async def execute(*args):
        effects.append(args)
        return AgentToolResult.from_text('effect')
    live = [True]
    port, backend, bound = await prepared_bridge(AgentTool('report', execute=execute), live)
    cancel = asyncio.Event()
    if reason == 'parent':
        live[0] = False
    elif reason == 'cancel':
        cancel.set()
    elif reason == 'settled':
        backend.state = replace(backend.state, settling=False, status='completed')
    elif reason == 'binding':
        port._bound.clear()
    else:
        backend.closed = True
    with pytest.raises((ValueError, asyncio.CancelledError)):
        await bound.execute('call', {}, cancel)
    assert not effects


@pytest.mark.parametrize('remote', [False, True])
async def test_bridge_rechecks_parent_after_original_validator_await(remote):
    started, release = asyncio.Event(), asyncio.Event()
    effects, validations = [], []
    async def validator(args):
        validations.append(args)
        started.set()
        await release.wait()
    async def execute(*args):
        effects.append(args)
        return AgentToolResult.from_text('effect')
    live = [True]
    _, _, bound = await prepared_bridge(AgentTool('report', execute=execute, validate_arguments=validator), live)
    task = (asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(bound.execute('call', {}))))
            if remote else asyncio.create_task(bound.execute('call', {})))
    try:
        await asyncio.wait_for(started.wait(), 2)
        live[0] = False
        release.set()
        with pytest.raises(ValueError, match='retired'):
            await task
        assert validations == [{}] and not effects
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_original_bound_capability_still_executes_once_for_live_running_parent():
    effects = []
    async def execute(*args):
        effects.append(args)
        return AgentToolResult.from_text('ACTUAL EFFECT')
    _, _, bound = await prepared_bridge(AgentTool('report', execute=execute), [True])
    result = await bound.execute('call', {'value': 'approved'})
    assert 'ACTUAL EFFECT' in str(result.content)
    assert len(effects) == 1 and effects[0][1] == {'value': 'approved'}


async def test_role_schema_and_original_validator_cannot_expand_in_place_after_admission():
    effects, validated = [], []
    def original_validator(params):
        validated.append('original')
        if params.get('action') != 'read':
            raise ValueError('original action restricted')
    async def execute(*args):
        effects.append(args)
        return AgentToolResult.from_text('effect')
    source = AgentTool('report', parameters={'type': 'object', 'properties': {'action': {'enum': ['read']}}},
                       execute=execute, validate_arguments=original_validator)
    _, _, bound = await prepared_bridge(source, [True])
    source.parameters['properties']['action']['enum'].append('write')
    source.validate_arguments = None
    assert bound.parameters['properties']['action']['enum'] == ['read']
    with pytest.raises(ValueError, match='restricted'):
        await bound.execute('call', {'action': 'write'})
    assert validated == ['original'] and not effects


async def test_role_without_validator_still_has_frozen_full_schema_at_effect():
    effects = []
    async def execute(*args):
        effects.append(args)
        return AgentToolResult.from_text('effect')
    source = AgentTool('report', parameters={'type': 'object', 'properties': {'action': {'enum': ['read']}}},
                       execute=execute)
    _, _, bound = await prepared_bridge(source, [True])
    source.parameters['properties']['action']['enum'].append('write')
    with pytest.raises(ValueError, match='schema'):
        await bound.execute('call', {'action': 'write'})
    assert not effects
