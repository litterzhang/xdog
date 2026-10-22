from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.worker import dispatch
from xdog.agent.mcp.stdio import McpError


@pytest.mark.parametrize('name', ['tool', 'tools', 'tool_snapshot', '_tool_ids', '_tool_revision',
                                '_advance_tool_revision'])
def test_tool_registration_api_is_absent_not_an_empty_compatibility_stub(name):
    api = ExtensionAPI({})
    assert not hasattr(api, name)
    with pytest.raises(AttributeError):
        getattr(api, name)
    assert importlib.util.find_spec('xdog.agent.extensions.tools') is None


@pytest.mark.parametrize('name', ['tools', 'sync_tools', 'aliases', 'tool_owner', 'accept_tool_registration',
                                '_aliases', '_tool_wrappers'])
def test_runtime_tool_projection_api_is_absent(tmp_path, name):
    runtime = ExtensionRuntime([], tmp_path / 'extensions', tmp_path)
    try:
        assert not hasattr(runtime, name)
        with pytest.raises(AttributeError):
            getattr(runtime, name)
    finally:
        runtime.close()


@pytest.mark.parametrize('method', ['tools/list', 'tools/call'])
@pytest.mark.parametrize('params', [{}, {'name': 'target', 'arguments': {}, '_xdog_tool_registration': 'a' * 32}])
async def test_worker_refuses_tool_protocol_even_with_legacy_registration_identity(method, params):
    api = ExtensionAPI({})
    with pytest.raises(ValueError, match='Unknown extension method'):
        await dispatch(api, method, params)
    assert not api.entries and not api.ui


async def test_handshake_admits_callback_only_extension_without_mcp_tool_discovery(tmp_path, monkeypatch):
    from xdog.agent.mcp.registry import McpRegistry
    from xdog.agent.mcp.stdio import StdioClient
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/sample', root / 'sample')
    runtime = ExtensionRuntime(['sample'], root, tmp_path)
    calls = []
    original = StdioClient._request_once
    async def request(self, method, params, **kwargs):
        calls.append(method)
        assert method not in {'tools/list', 'tools/call'}
        result = await original(self, method, params, **kwargs)
        assert 'xdog_tool_snapshot' not in result
        return result
    async def forbidden(*args):
        pytest.fail('Extension handshake must not discover an MCP tool catalog')
    monkeypatch.setattr(StdioClient, '_request_once', request)
    monkeypatch.setattr(McpRegistry, '_discover', forbidden)
    try:
        await runtime.initialize()
        assert runtime.snapshot()['extensions'] == [{'name': 'sample', 'state': 'active'}]
        client = runtime._manager._clients['sample']
        assert client.state == 'connected' and client.capabilities == {}
        assert not client.inputs.active and not client.root_capability.active
        assert calls[0] == 'server/discover' and 'initialize' not in calls
        assert await runtime.command('ext-sample-hello', 'data') == 'EXTENSION-COMMAND-data'
        assert await runtime.pre('filesystem', {'value': 'veto'}) is None
        await runtime.manage('reload', 'sample')
        assert runtime.snapshot()['extensions'][0]['state'] == 'active'
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('fixture', ['toolmock', 'toolretirement'])
async def test_legacy_tool_setup_is_rejected_not_silently_ignored(tmp_path, fixture):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions' / fixture, root / fixture)
    runtime = ExtensionRuntime([fixture], root, tmp_path)
    try:
        await runtime.initialize()
        if fixture == 'toolmock':
            # The legacy fixture defers registration until a command callback.
            assert runtime.snapshot()['extensions'][0]['state'] == 'active'
            with pytest.raises(McpError, match='callback failed'):
                await runtime.command('ext-toolmock-enable', '')
        assert runtime.snapshot()['extensions'] == [{'name': fixture, 'state': 'failed'}]
        client = runtime._manager._clients.get(fixture)
        assert client is None or client.state == 'disconnected'
        assert not runtime.commands() and not hasattr(runtime, 'tools')
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('method', ['tools/list', 'tools/call'])
async def test_real_worker_rejects_tool_requests_and_host_validation_fails_closed(tmp_path, method):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/sample', root / 'sample')
    runtime = ExtensionRuntime(['sample'], root, tmp_path)
    try:
        await runtime.initialize()
        client = runtime._manager._clients['sample']
        process = client.process
        info = await runtime._manager._bridge(runtime._rpc('sample', 'xdog/info', {}))
        assert 'tool_revision' not in info and 'xdog_tool_snapshot' not in info
        with pytest.raises(McpError, match='callback failed'):
            await runtime._manager._bridge(runtime._rpc('sample', method, {
                'name': 'echo', 'arguments': {'value': 'must not execute'},
                '_xdog_tool_registration': 'a' * 32}))
        assert process.returncode is not None and client.process is None
        assert runtime.snapshot()['extensions'][0]['state'] == 'failed'
        with pytest.raises(ValueError, match='validation unavailable'):
            await runtime.pre('filesystem', {})
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('changes', [
    {'capabilities': {'tools': {}}}, {'capabilities': {'resources': {}}},
    {'capabilities': []}, {'supportedVersions': ['unsupported']},
])
async def test_extension_handshake_rejects_mcp_servers_and_invalid_versions(tmp_path, monkeypatch, changes):
    from xdog.agent.mcp.stdio import StdioClient
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/sample', root / 'sample')
    runtime = ExtensionRuntime(['sample'], root, tmp_path)
    original = StdioClient._request_once
    processes = []
    async def altered(self, method, params, **kwargs):
        value = await original(self, method, params, **kwargs)
        if method == 'server/discover':
            processes.append(self.process)
            return {**value, **changes}
        return value
    monkeypatch.setattr(StdioClient, '_request_once', altered)
    try:
        await runtime.initialize()
        assert runtime.snapshot()['extensions'][0]['state'] == 'failed'
        assert not runtime._manager._clients and not runtime.commands()
        assert len(processes) == 1 and processes[0].returncode is not None
    finally:
        await runtime.aclose()


async def test_same_agent_loop_reads_current_inventory_after_tool_registration_changes():
    from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    contexts, executed = [], []
    async def first(*args, **kwargs):
        executed.append('first')
        owner.set_tools((next_tool,))
        return AgentToolResult(content=(TextContent(text='new tool registered'),))
    async def second(*args, **kwargs):
        executed.append('second')
        return AgentToolResult(content=(TextContent(text='done'),))
    next_tool = AgentTool(name='second', parameters={'type': 'object'}, execute=second)
    def stream(model, context, options):
        contexts.append(context)
        names = {tool.name for tool in context.tools or ()}
        if len(contexts) == 1:
            assert names == {'first'}
            message = AssistantMessage(content=(ToolCall(id='one', name='first', arguments={}),))
        elif len(contexts) == 2:
            assert names == {'second'}
            message = AssistantMessage(content=(ToolCall(id='two', name='second', arguments={}),))
        else:
            message = AssistantMessage(content=(TextContent(text='complete'),))
        return EventStream.empty(message)
    owner = Agent(stream, config=AgentConfig(model='test/model'),
                  tools=(AgentTool(name='first', parameters={'type': 'object'}, execute=first),))
    async for _event in await owner.prompt('run'):
        pass
    assert executed == ['first', 'second'] and len(contexts) == 3
