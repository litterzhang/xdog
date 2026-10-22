"""Owner-local MCP registry: inert admission, real identity and delegated manifests."""
from __future__ import annotations

import asyncio
import gc
import importlib.util
import json
import sys
import threading
import weakref
from dataclasses import replace
from pathlib import Path

import pytest
from xdog.agent.core import AgentTool
from xdog.agent.mcp import McpError, McpRegistry, McpServerConfig

FIXTURE = Path(__file__).parent / 'fixtures/mcp_server.py'


def config(tmp_path, name='peer'):
    return {'command': sys.executable, 'args': [str(FIXTURE), 'modern', str(tmp_path / (name + '.log'))],
            'allow_tools': True}


def frames(tmp_path):
    return [json.loads(line) for line in (tmp_path / 'peer.log').read_text().splitlines()]


def registry(tmp_path):
    return McpRegistry({'peer': config(tmp_path)}, tmp_path)


def test_no_retired_manager_module_or_class_alias():
    import xdog.agent.mcp as mcp
    assert not hasattr(mcp, 'McpManager')
    assert importlib.util.find_spec('xdog.agent.mcp.manager') is None


async def test_default_and_incremental_registration_are_inert_and_owned(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first, second = McpRegistry(), McpRegistry()
    raw = config(tmp_path)
    try:
        first.register('peer', raw)
        raw['args'].append('caller-mutation')
        assert first.cwd == tmp_path and first.configs['peer'].command[-1] != 'caller-mutation'
        assert not second.configs and first.credential_store is None
        parsed = McpServerConfig.parse('other', config(tmp_path, 'other'))
        first.register('other', parsed)
        assert first.configs['other'] is parsed
        assert not first.tools() and not first.context_aliases()
        assert [row['state'] for row in first.status()] == ['disconnected', 'disconnected']
        assert first._loop is None and first._thread is None and first._schemas._process is None
        assert not list(tmp_path.iterdir())
        with pytest.raises(ValueError, match='already registered'):
            first.register('peer', config(tmp_path))
        with pytest.raises(ValueError, match='identity'):
            first.register('alias', parsed)
        for name, raw_config in [('invalid id', config(tmp_path)), ('invalid', {'command': ''}),
                                 ('invalid', {'transport': 'arbitrary'})]:
            with pytest.raises(ValueError):
                first.register(name, raw_config)
        assert set(first.configs) == {'peer', 'other'}
    finally:
        await first.aclose()
        await second.aclose()
    assert first._thread is None
    with pytest.raises(McpError, match='closed'):
        first.register('third', config(tmp_path))


async def test_proxy_cache_uses_real_identity_and_retains_retired_ownership(tmp_path):
    owner = registry(tmp_path)
    try:
        await owner.connect('peer')
        originals = owner.tools()
        assert all(a is b for a, b in zip(originals, owner.tools(), strict=True))
        tool = originals[0]
        assert owner.allowed(tool.name) and owner.owns_tool(tool) and owner.is_current_tool(tool)
        forged = replace(tool)
        assert forged == tool and not owner.owns_tool(forged) and not owner.is_current_tool(forged)
        assert not owner.owns_tool(None)
        tool.parameters['forged'] = True
        assert owner.tools()[0] is tool  # Model data edits do not mint execution identity.
        await tool.validate_arguments({'value': 'valid'})
        await owner.refresh('peer')
        current = owner.tools()[0]
        assert current.name == tool.name and current is not tool
        assert current.parameters.get('forged') is None
        assert owner.owns_tool(tool) and not owner.is_current_tool(tool)
        before = frames(tmp_path)
        with pytest.raises(McpError, match='original owner changed'):
            await tool.validate_arguments({'value': 'old-validator'})
        with pytest.raises(McpError, match='original owner changed'):
            await tool.execute('old-executor', {'value': 'old-executor'})
        assert frames(tmp_path) == before
        assert (await current.execute('current', {'value': 'new'})).content[0].text == 'new'
        await owner.disconnect('peer')
        assert not owner.tools() and owner.owns_tool(current) and not owner.is_current_tool(current)
        await owner.connect('peer')
        successor = owner.tools()[0]
        assert successor is not current and successor.name == current.name
        before = frames(tmp_path)
        with pytest.raises(McpError, match='original owner changed'):
            await current.execute('never-successor', {'value': 'stale'})
        assert frames(tmp_path) == before
        assert (await successor.execute('successor', {'value': 'owned'})).content[0].text == 'owned'
    finally:
        await owner.aclose()
    assert not owner.tools() and owner.owns_tool(successor) and not owner.is_current_tool(successor)
    with pytest.raises(McpError):
        await successor.validate_arguments({'value': 'closed'})
    assert not owner._thread.is_alive()


@pytest.mark.parametrize('operation', ['validate', 'execute'])
@pytest.mark.parametrize('retire', ['refresh', 'reconnect'])
async def test_retirement_while_schema_validation_awaits_never_adopts_new_spec_or_client(
    tmp_path, operation, retire, monkeypatch
):
    owner = registry(tmp_path)
    entered, release = threading.Event(), threading.Event()
    pending = None
    try:
        await owner.connect('peer')
        old = owner.tools()[0]
        original = owner._schemas.validate
        async def held(document, params, *, label):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.001)
            await original(document, params, label=label)
        monkeypatch.setattr(owner._schemas, 'validate', held)
        pending = asyncio.create_task(old.validate_arguments({'value': 'held'}) if operation == 'validate'
                                      else old.execute('held', {'value': 'held'}))
        assert await asyncio.to_thread(entered.wait, 5)
        if retire == 'refresh':
            await owner.refresh('peer')
        else:
            await owner.disconnect('peer')
            await owner.connect('peer')
        release.set()
        with pytest.raises(McpError, match='original owner changed'):
            await pending
        assert not any(frame['method'] == 'tools/call' for frame in frames(tmp_path))
        assert not owner.is_current_tool(old) and owner.owns_tool(old)
        monkeypatch.setattr(owner._schemas, 'validate', original)
        assert (await owner.tools()[0].execute('fresh', {'value': 'fresh'})).content[0].text == 'fresh'
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await owner.aclose()


async def test_executor_rechecks_original_identity_after_validation_before_remote_admission(tmp_path, monkeypatch):
    owner = registry(tmp_path)
    entered, release = threading.Event(), threading.Event()
    pending = None
    try:
        await owner.connect('peer')
        old = owner.tools()[0]
        original = owner._call
        async def held(spec, args, client):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.001)
            return await original(spec, args, client)
        monkeypatch.setattr(owner, '_call', held)
        pending = asyncio.create_task(old.execute('held', {'value': 'never sent'}))
        assert await asyncio.to_thread(entered.wait, 5)
        await owner.disconnect('peer')
        await owner.connect('peer')
        release.set()
        with pytest.raises(McpError, match='original owner changed'):
            await pending
        assert not any(frame['method'] == 'tools/call' for frame in frames(tmp_path))
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await owner.aclose()


async def test_delegated_manifest_is_original_identity_atomic_and_connection_free(tmp_path, monkeypatch):
    first, second = AgentTool(name='first'), AgentTool(name='second')
    delegated = McpRegistry.from_tools(tool for tool in (first, second))
    configured = registry(tmp_path)
    try:
        assert delegated.tools()[0] is first and delegated.tools()[1] is second
        assert delegated.owns_tool(first) and delegated.is_current_tool(first)
        assert not delegated.owns_tool(replace(first))
        assert not delegated.configs and delegated.credential_store is None and not delegated.status()
        assert delegated._thread is None and delegated._schemas._process is None
        with pytest.raises(ValueError, match='unique'):
            delegated.replace_delegated_tools([first, replace(first)])
        assert delegated.tools() == (first, second)
        for bad in ([first, first], [object()], [AgentTool(name='')]):
            with pytest.raises(ValueError):
                McpRegistry.from_tools(bad)
        delegated.replace_delegated_tools([second])
        assert delegated.tools() == (second,) and delegated.owns_tool(first)
        assert not delegated.is_current_tool(first) and delegated.is_current_tool(second)
        with pytest.raises(McpError, match='cannot register'):
            delegated.register('peer', config(tmp_path))
        with pytest.raises(McpError, match='from_tools'):
            configured.replace_delegated_tools([first])
        assert configured.configs and not configured.tools()
        def forbidden():
            pytest.fail('Delegated transport rejection must not start an owner/thread')
        monkeypatch.setattr(delegated, '_ensure_loop', forbidden)
        with pytest.raises(McpError, match='no transport owner'):
            await delegated.connect('peer')
        with pytest.raises(McpError, match='no transport owner'):
            await delegated.refresh('peer')
        assert delegated._thread is None and delegated._loop is None
        delegated.replace_delegated_tools([])
        assert not delegated.tools() and delegated.owns_tool(second)
    finally:
        await delegated.aclose()
        await configured.aclose()
    assert delegated.owns_tool(first) and not delegated.is_current_tool(second)
    with pytest.raises(McpError, match='closed'):
        delegated.replace_delegated_tools([])


async def test_delegated_close_never_closes_or_revokes_parent_connection(tmp_path):
    owner = registry(tmp_path)
    delegated = None
    try:
        await owner.connect('peer')
        original = owner.tools()[0]
        client = owner._clients['peer']
        process = client.process
        delegated = McpRegistry.from_tools([original])
        assert delegated.tools()[0] is original
        assert (await delegated.tools()[0].execute('child', {'value': 'delegated'})).content[0].text == 'delegated'
        await delegated.aclose()
        assert client.state == 'connected' and process.returncode is None
        assert owner.is_current_tool(original) and owner.owns_tool(original)
        assert (await original.execute('parent', {'value': 'still owned'})).content[0].text == 'still owned'
        assert delegated._thread is None and not delegated.tools()
        assert delegated.owns_tool(original)
    finally:
        if delegated is not None:
            await delegated.aclose()
        await owner.aclose()


async def test_retired_identity_bookkeeping_does_not_retain_unused_proxy_objects():
    first = AgentTool(name='first')
    reference = weakref.ref(first)
    delegated = McpRegistry.from_tools([first])
    delegated.replace_delegated_tools([])
    del first
    gc.collect()
    assert reference() is None and not delegated._owned_tools
    await delegated.aclose()
