"""Sampling is unsupported, never advertised/executed or interpreted as Elicitation."""
from __future__ import annotations

import copy
import importlib.util
import json

import httpx
import pytest
from xdog.agent.mcp import McpRegistry
from xdog.agent.mcp.client_capabilities import RootCapability, reverse_response
from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.http import HttpClient
from xdog.agent.mcp.input_requests import InputRequests
from xdog.agent.mcp.stdio import McpError

from packages.agent.tests.mcp_helpers import reverse_peer_config, until
from packages.agent.tests.test_mcp_elicitation import form


def sampling(params=None, identifier="unsupported-sampling"):
    return {"jsonrpc": "2.0", "id": identifier, "method": "sampling/createMessage",
            "params": params if params is not None else {"messages": [], "maxTokens": 20}}


def test_sampling_public_implementation_and_manager_registration_are_absent(tmp_path):
    for name in ("sampling", "sampling_owner", "sampling_executor", "sampling_context"):
        assert importlib.util.find_spec("xdog.agent.mcp." + name) is None
    manager = McpRegistry({}, tmp_path)
    for name in ("register_sampling", "answer_sampling", "detach_sampling", "_sampling"):
        assert not hasattr(manager, name)
    assert manager._thread is None  # No model, transport, helper or background loop on construction.


@pytest.mark.parametrize("params", [{}, {"messages": [], "maxTokens": 20}, {"task": {}},
                                    {"task": {"ttl": 100}},
                                    {"_meta": {"io.modelcontextprotocol/related-task": {"taskId": "foreign"}}}])
def test_sampling_reverse_refusal_never_borrows_configured_root_authority(params):
    roots = RootCapability((("file:///explicit", "Explicit"),))
    roots.active = True
    packet = sampling(params)
    expected = {"error": {"code": -32601, "message": "Sampling is not supported"}}
    assert reverse_response(packet) == roots.response(packet) == expected
    assert roots.roots == roots.ceiling


@pytest.mark.parametrize("identifier", [True, False, None, [], {}, "", "x" * 129, 2**53])
def test_invalid_sampling_identity_never_reflected(identifier):
    with pytest.raises(ValueError, match="identity"):
        reverse_response(sampling(identifier=identifier))


async def test_modern_sampling_whole_round_refuses_without_model_review_private_data_or_resume():
    roots = RootCapability((("file:///explicit", "Explicit"),))
    roots.active = True
    inputs = InputRequests(roots)
    inputs.active = True
    sent, callbacks = [], []
    source = form()
    async def send(value):
        sent.append(copy.deepcopy(value))
        return {"resultType": "input_required", "requestState": "opaque-not-resumed", "inputRequests": {
            "elicit": {"method": source["method"], "params": source["params"]},
            "sampling": {"method": "sampling/createMessage", "params": {"messages": [], "maxTokens": 20}}}}
    try:
        assert inputs.advertised() == {"roots": {}}
        assert not hasattr(inputs, "sampling_handler") and not hasattr(inputs, "sampling_alive")
        with pytest.raises(McpError, match="not declared"):
            await inputs.run("tools/call", {"name": "owned", "arguments": {}}, send)
        assert len(sent) == 1 and not callbacks and not inputs._jobs
        assert "inputResponses" not in sent[0]
    finally:
        await inputs.close()


async def test_owned_stdio_sampling_receives_explicit_refusal_and_reader_stays_live(tmp_path):
    configs, log = reverse_peer_config(tmp_path)
    manager = McpRegistry(configs, tmp_path)
    try:
        await manager.connect("owned")
        client = manager._clients["owned"]
        await until(lambda: any(json.loads(row).get("id") == "sample" for row in log.read_text().splitlines()))
        assert await manager._bridge(client.request("ping", {})) == {}
        frames = [json.loads(row) for row in log.read_text().splitlines()]
        initial = next(row for row in frames if row.get("method") == "initialize")
        assert "sampling" not in initial["params"]["capabilities"]
        replies = [row for row in frames if row.get("id") == "sample"]
        assert replies == [{"jsonrpc": "2.0", "id": "sample", "error": {
            "code": -32601, "message": "Sampling is not supported"}}]
        assert not hasattr(client, "reverse") and not client.inputs._jobs
        assert manager.status()[0]["client_capability_support"]["sampling"] == "unsupported"
    finally:
        await manager.aclose()


async def test_mock_http_sampling_explicit_refusal_preserves_correlated_result_without_replay():
    sent = []
    def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(202)
    client = HttpClient(McpServerConfig.parse("owned", {"transport": "http", "url": "http://127.0.0.1/mcp"}))
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    client.state, client.version = "connected", "2025-11-25"
    try:
        assert await client._message(sampling(), None, 200, modern=False) is None
        assert sent == [{"jsonrpc": "2.0", "id": "unsupported-sampling", "error": {
            "code": -32601, "message": "Sampling is not supported"}}]
        assert await client._message({"jsonrpc": "2.0", "id": 42, "result": {"content": []}},
                                     42, 200, modern=False) == {"content": []}
        assert len(sent) == 1 and not hasattr(client, "reverse")
    finally:
        await client.close()
