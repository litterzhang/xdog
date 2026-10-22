"""Removed capabilities cannot construct handlers, tasks or model/tool continuations."""
from __future__ import annotations

import importlib.util
import json

import httpx
import pytest
from xdog.agent.mcp import McpRegistry
from xdog.agent.mcp.client_capabilities import RootCapability, reverse_response
from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.http import HttpClient
from xdog.agent.mcp.input_requests import InputRequests
from xdog.agent.mcp.stdio import McpError, StdioClient

REMOVED = ['sampling/createMessage', 'elicitation/create', 'tasks/list', 'tasks/get', 'tasks/result', 'tasks/cancel']


def test_removed_runtime_modules_and_public_registration_are_absent(tmp_path):
    for module in ('sampling', 'sampling_owner', 'sampling_context', 'sampling_executor', 'elicitation',
                   'elicitation_owner', 'task_registry', 'task_notifications', 'reverse_requests'):
        assert importlib.util.find_spec('xdog.agent.mcp.' + module) is None
    manager = McpRegistry({}, tmp_path)
    for name in ('register_sampling', 'answer_sampling', 'detach_sampling', '_sampling', 'register_elicitation',
                 'answer_elicitation', 'detach_elicitation', '_elicitation', 'manage_roots', 'detach_roots'):
        assert not hasattr(manager, name)
    assert manager._thread is None
    config = McpServerConfig.parse('owned', {'command': 'not-started'})
    client = StdioClient(config, tmp_path)
    assert client.process is None and not hasattr(client, 'reverse')
    inputs = InputRequests(client.root_capability)
    for name in ('elicitation_handler', 'elicitation_alive', 'sampling_handler', 'reverse', '_handle', 'withdraw'):
        assert not hasattr(inputs, name)
    assert inputs.advertised() == {} and not inputs._jobs


@pytest.mark.parametrize('method', REMOVED)
@pytest.mark.parametrize('params', [{}, {'task': {}}, {'task': {'ttl': 100}}, {'taskId': 'foreign'},
                                  {'_meta': {'io.modelcontextprotocol/related-task': {'taskId': 'current'}}}])
def test_legacy_removed_family_refusal_cannot_borrow_roots_or_task_identity(method, params):
    root = RootCapability((('file:///explicit/root', 'Explicit'),))
    root.active = True
    packet = {'jsonrpc': '2.0', 'id': 'old', 'method': method, 'params': params}
    result = root.response(packet)
    assert result == reverse_response(packet) and result['error']['code'] == -32601
    assert 'result' not in result
    assert root.roots == root.ceiling == (('file:///explicit/root', 'Explicit'),)
    assert root.advertised() == {'roots': {}}


@pytest.mark.parametrize('identifier', [True, False, None, [], {}, '', 'x' * 129, 2**53])
def test_removed_capability_invalid_identity_never_reflected(identifier):
    for method in REMOVED:
        with pytest.raises(ValueError, match='identity'):
            reverse_response({'jsonrpc': '2.0', 'id': identifier, 'method': method})


@pytest.mark.parametrize('changes', [{'jsonrpc': 'wrong'}, {'params': []}, {'error': {}},
                                    {'method': ''}, {'method': False}, {'unexpected': 'field'}])
def test_reverse_shape_validation_still_precedes_reflection_or_refusal(changes):
    packet = {'jsonrpc': '2.0', 'id': 'old', 'method': 'elicitation/create', 'params': {}}
    assert reverse_response({**packet, **changes})['error']['code'] == -32600


@pytest.mark.parametrize('method', REMOVED)
async def test_mock_http_explicit_refusal_preserves_correlation_without_callback_or_private_result(method):
    sent = []
    def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(202)
    client = HttpClient(McpServerConfig.parse('owned', {'transport': 'http', 'url': 'http://127.0.0.1/mcp'}))
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    client.version, client.state = '2025-11-25', 'connected'
    packet = {'jsonrpc': '2.0', 'id': 'old', 'method': method, 'params': {'task': {}, 'taskId': 'forged'}}
    try:
        assert not hasattr(client, 'reverse')
        assert await client._message(packet, None, 200, modern=False) is None
        assert sent == [{'jsonrpc': '2.0', 'id': 'old', **reverse_response(packet)}]
        assert sent[0]['error']['code'] == -32601
        assert await client._message({'jsonrpc': '2.0', 'id': 42, 'result': {'content': []}},
                                     42, 200, modern=False) == {'content': []}
        with pytest.raises(McpError, match='unrelated'):
            await client._message(packet, None, 200, modern=True)
        assert len(sent) == 1 and not client.inputs._jobs
    finally:
        await client.close()


async def test_obsolete_completion_and_cancel_notices_cannot_start_or_resume_work():
    sent = []
    client = HttpClient(McpServerConfig.parse('owned', {'transport': 'http', 'url': 'http://127.0.0.1/mcp'}))
    def forbidden(request):
        sent.append(request)
        raise AssertionError('Removed notification cannot perform an HTTP request')
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(forbidden))
    client.version, client.state = '2025-11-25', 'connected'
    try:
        for method in ('notifications/elicitation/complete', 'notifications/tasks/status', 'notifications/cancelled'):
            packet = {'jsonrpc': '2.0', 'method': method, 'params': {'requestId': 'old', 'taskId': 'old'}}
            assert await client._message(packet, None, 200, modern=False) is None
            with pytest.raises(McpError, match='unrelated'):
                await client._message(packet, None, 200, modern=True)
        assert not sent and not client.inputs._jobs
    finally:
        await client.close()
