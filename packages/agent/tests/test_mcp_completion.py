from __future__ import annotations

import importlib.util
import json
import sys
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from xdog.agent.mcp import McpError, McpRegistry
from xdog.agent.mcp.completion import decode_payload, request_arguments, template_arguments, values

FIXTURE = Path(__file__).parent / "fixtures/mcp_completion_peer.py"
TEMPLATE = "fixture://document/{name}{?format}"


def stdio(tmp_path, mode="modern"):
    return McpRegistry(
        {"peer": {"command": sys.executable, "args": [str(FIXTURE), mode, str(tmp_path / "log")]}}, tmp_path
    )


@pytest.mark.parametrize(
    "name,argument,context",
    [
        ("not-advertised", "topic", {}),
        ("review", "unknown", {}),
        ("review", "topic", {"unknown": "x"}),
        ("review", "topic", {"topic": "current must not be context"}),
    ],
)
async def test_unadvertised_references_and_context_are_rejected_without_completion_call(
    tmp_path, name, argument, context
):
    manager = stdio(tmp_path)
    try:
        await manager.connect("peer")
        with pytest.raises(McpError):
            await manager.complete("peer", "prompt", name, argument, "", context)
        assert not any(
            json.loads(line)["method"] == "completion/complete" for line in (tmp_path / "log").read_text().splitlines()
        )
    finally:
        await manager.aclose()


def test_invalid_results_are_not_coerced_truncated_or_silently_deferred() -> None:

    def _check_case(bad):
        with pytest.raises(McpError):
            values(bad)

    for bad in [
        None,
        [],
        {},
        {"completion": []},
        {"resultType": "task", "completion": {"values": []}},
        {"completion": {"values": ["x"] * 101}},
        {"completion": {"values": [True]}},
        {"completion": {"values": ["x" * 8193]}},
        {"completion": {"values": ["x"], "total": 0}},
        {"completion": {"values": [], "total": True}},
        {"completion": {"values": [], "total": 2**53}},
        {"completion": {"values": [], "hasMore": "true"}},
    ]:
        _check_case(bad)


def test_template_catalog_variables_and_request_codec_are_bounded_without_reserved_key_collision():
    assert template_arguments("fixture://{+path}/{name:3}{?a.b,var%20name,list*}{name}") == (
        "path",
        "name",
        "a.b",
        "var%20name",
        "list",
    )
    assert request_arguments({"argument": "topic", "value": "p", "context": '{"context":"named context value"}'}) == (
        "topic",
        "p",
        {"context": "named context value"},
    )
    for request in (
        {},
        {"argument": "x", "value": "v", "context": "[]"},
        {"argument": "x", "value": "v", "context": "{}", "extra": "unexpected"},
        {"argument": "x", "value": "v", "context": '{"x":true}'},
    ):
        with pytest.raises(McpError):
            request_arguments(request)
    for template in ("{", "{}", "{name:0}", "{x**}", "{=name}"):
        with pytest.raises(McpError):
            template_arguments(template)


@pytest.fixture
def http_peer():
    spec = importlib.util.spec_from_file_location("independent_completion_peer", FIXTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = {"requests": [], "headers": [], "bad": None, "legacy": False, "gets": 0, "completion_only": False}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            state["gets"] += 1
            self.send_error(405)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append(request)
            state["headers"].append(dict(self.headers))
            if "id" not in request:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            response = module.response(
                request,
                legacy=state["legacy"],
                bad=state["bad"] if request["method"] == "completion/complete" else None,
                completion_only=state.get("completion_only", False),
                invalid_completion=state.get("invalid_completion", False),
            )
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_completion_ipc_codec_rejects_malformed_context_scope_and_counts() -> None:

    def _check_case(mutate):
        from xdog.agent.mcp.completion import McpCompletion

        value = json.loads(
            json.dumps(asdict(McpCompletion("peer", "prompt", "review", "topic", "", (), ("first",), 1)))
        )
        with pytest.raises(McpError):
            decode_payload({**value, **mutate})

    for mutate in [
        {"kind": []},
        {"argument": ""},
        {"value": "x" * 8193},
        {"context": [["language", True]]},
        {"context": [["language", "python"], ["language", "rust"]]},
        {"values": [None]},
        {"has_more": 1},
        {"total": -1},
    ]:
        _check_case(mutate)


@pytest.mark.parametrize('mode', ['modern', 'legacy', 'completion-only'])
async def test_opaque_resource_reference_does_not_invent_catalog_or_argument_schema(tmp_path, mode):
    manager = stdio(tmp_path, mode)
    uri = 'opaque://dynamic/not-listed/{not-expanded}'
    try:
        await manager.connect('peer')
        result = await manager.complete('peer', 'resource', uri, 'opaque-field', 'pre', {'language': '中文'})
        assert result.kind == 'resource' and result.name == uri and result.values == ('completed value', '第二项')
        assert decode_payload(json.loads(json.dumps(asdict(result)))) == result
        frames = [json.loads(line) for line in (tmp_path / 'log').read_text().splitlines()]
        calls = [frame for frame in frames if frame['method'] == 'completion/complete']
        assert len(calls) == 1
        assert calls[0]['params']['ref'] == {'type': 'ref/resource', 'uri': uri}
        assert calls[0]['params']['context'] == {'arguments': {'language': '中文'}}
        assert not any(frame['method'] in ('resources/list', 'resources/templates/list', 'resources/read',
                                           'prompts/list', 'prompts/get', 'tools/call') for frame in frames)
    finally:
        await manager.aclose()


async def test_opaque_current_argument_context_conflict_refused_without_remote_post(tmp_path):
    manager = stdio(tmp_path)
    try:
        await manager.connect('peer')
        with pytest.raises(McpError, match='cannot also be context'):
            await manager.complete('peer', 'resource', 'opaque:ref', 'opaque-field', '', {'opaque-field': 'conflict'})
        assert not any(json.loads(line)['method'] == 'completion/complete'
                       for line in (tmp_path / 'log').read_text().splitlines())
    finally:
        await manager.aclose()


def test_local_completion_document_labels_operator_edits_without_resource_or_path_interpretation():
    from xdog.agent.mcp.context import resource_completion_document
    document = resource_completion_document('peer', 'file:///not-opened/private',
        {'argument': 'opaque-field', 'value': '../../not-a-uri', 'context': '{"language":"中文"}'})
    assert document.kind == 'resource-completion' and document.blocks[0].role == 'user'
    assert 'Operator-selected/edited' in document.text and 'NOT a resource read or resolved URI' in document.text
    assert '../../not-a-uri' in document.text and 'language' in document.text
    assert '[User-selected untrusted MCP resource-completion from peer]' in document.draft_text()
    with pytest.raises(McpError):
        resource_completion_document('peer', 'opaque:ref', {'argument': '', 'value': 'x', 'context': '{}'})


async def test_completion_only_stdio_metadata_requires_an_object_capability(tmp_path):
    manager = stdio(tmp_path, 'invalid-completion-capability')
    try:
        with pytest.raises(McpError, match='supported capability'):
            await manager.connect('peer')
        assert manager.status()[0]['state'] == 'disconnected' and not manager.tools()
        assert not any(json.loads(line)['method'] == 'completion/complete'
                       for line in (tmp_path / 'log').read_text().splitlines())
    finally:
        await manager.aclose()
