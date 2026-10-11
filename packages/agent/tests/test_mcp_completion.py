from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from xdog.agent.mcp import McpError, McpManager
from xdog.agent.mcp.completion import decode_payload, request_arguments, template_arguments, values

FIXTURE = Path(__file__).parent / "fixtures/mcp_completion_peer.py"
TEMPLATE = "fixture://document/{name}{?format}"


def stdio(tmp_path, mode="modern"):
    return McpManager(
        {"peer": {"command": sys.executable, "args": [str(FIXTURE), mode, str(tmp_path / "log")]}}, tmp_path
    )


@pytest.mark.parametrize("mode", ["modern", "legacy"])
async def test_owned_stdio_prompt_and_template_completion_reference_context_are_inert(mode, tmp_path):
    manager = stdio(tmp_path, mode)
    try:
        await manager.connect("peer")
        prompt = await manager.complete("peer", "prompt", "review", "topic", "co", {"language": "python"})
        assert prompt.values == ("completed value", "第二项") and prompt.total == 4 and prompt.has_more
        assert prompt.context == (("language", "python"),) and not manager.tools()
        assert decode_payload(json.loads(json.dumps(asdict(prompt)))) == prompt
        await asyncio.sleep(0.21)
        template = await manager.complete("peer", "template", TEMPLATE, "format", "js", {"name": "selected"})
        assert template.kind == "template" and template.value == "js"
        frames = [json.loads(line) for line in (tmp_path / "log").read_text().splitlines()]
        calls = [frame for frame in frames if frame["method"] == "completion/complete"]
        assert len(calls) == 2
        assert {key: item for key, item in calls[0]["params"].items() if key != "_meta"} == {
            "ref": {"type": "ref/prompt", "name": "review"},
            "argument": {"name": "topic", "value": "co"},
            "context": {"arguments": {"language": "python"}},
        }
        assert calls[1]["params"]["ref"] == {"type": "ref/resource", "uri": TEMPLATE}
        assert not any(frame["method"] in ("tools/call", "prompts/get", "resources/read") for frame in frames)
    finally:
        await manager.aclose()


async def test_missing_capability_and_rate_limit_refuse_without_redeeming_completion(tmp_path):
    manager = stdio(tmp_path, "no-completions")
    try:
        await manager.connect("peer")
        with pytest.raises(McpError, match="advertising completions"):
            await manager.complete("peer", "prompt", "review", "topic", "")
        assert not any(
            json.loads(line)["method"] == "completion/complete" for line in (tmp_path / "log").read_text().splitlines()
        )
    finally:
        await manager.aclose()
    manager = stdio(tmp_path)
    try:
        await manager.connect("peer")
        await manager.complete("peer", "prompt", "review", "topic", "")
        started = asyncio.get_running_loop().time()
        pending = asyncio.create_task(manager.complete("peer", "prompt", "review", "topic", "second"))
        await asyncio.sleep(0.02)
        queued = asyncio.create_task(manager.complete("peer", "prompt", "review", "topic", "third"))
        await asyncio.sleep(0.02)
        with pytest.raises(McpError, match="one successor are already pending"):
            await manager.complete("peer", "prompt", "review", "topic", "fourth")
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert (await pending).value == "second"
        assert asyncio.get_running_loop().time() - started >= 0.15
    finally:
        await manager.aclose()


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


async def test_canceled_completion_settles_owner_and_late_reply_cannot_deliver_to_successor(tmp_path):
    manager = stdio(tmp_path)
    try:
        await manager.connect("peer")
        task = asyncio.create_task(manager.complete("peer", "prompt", "review", "topic", "delay"))
        async with asyncio.timeout(3):
            while not any(
                json.loads(line)["method"] == "completion/complete"
                for line in (tmp_path / "log").read_text().splitlines()
            ):
                await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        await asyncio.sleep(0.21)
        result = await manager.complete("peer", "prompt", "review", "topic", "fresh")
        assert result.value == "fresh"
        await asyncio.sleep(0.65)
        frames = [json.loads(line) for line in (tmp_path / "log").read_text().splitlines()]
        assert len([frame for frame in frames if frame["method"] == "completion/complete"]) == 2
    finally:
        await manager.aclose()


@pytest.mark.parametrize(
    "bad",
    [
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
    ],
)
def test_invalid_results_are_not_coerced_truncated_or_silently_deferred(bad):
    with pytest.raises(McpError):
        values(bad)


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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


@pytest.mark.parametrize("legacy", [False, True])
async def test_owned_http_modern_and_legacy_completion_context_headers_and_no_get(tmp_path, http_peer, legacy):
    url, state = http_peer
    state["legacy"] = legacy
    manager = McpManager({"peer": {"transport": "http", "url": url}}, tmp_path)
    try:
        await manager.connect("peer")
        result = await manager.complete("peer", "template", TEMPLATE, "name", "co")
        assert result.values[1] == "第二项" and not manager.tools()
        if not legacy:
            assert state["gets"] == 0  # Legacy separately owns its existing notification GET.
        index = next(i for i, frame in enumerate(state["requests"]) if frame["method"] == "completion/complete")
        assert state["requests"][index]["params"]["argument"] == {"name": "name", "value": "co"}
        assert "context" not in state["requests"][index]["params"]
        if not legacy:
            assert state["headers"][index]["Mcp-Method"] == "completion/complete"
        await asyncio.sleep(0.21)
        state["bad"] = {"completion": {"values": ["x"], "total": False}}
        with pytest.raises(McpError):
            await manager.complete("peer", "prompt", "review", "topic", "co")
        assert sum(frame["method"] == "completion/complete" for frame in state["requests"]) == 2
    finally:
        await manager.aclose()


async def test_disconnect_settles_active_and_queued_completions_without_replay(tmp_path):
    manager = stdio(tmp_path)
    try:
        await manager.connect('peer')
        active = asyncio.create_task(manager.complete('peer', 'prompt', 'review', 'topic', 'delay'))
        async with asyncio.timeout(3):
            while not any(json.loads(line)['method'] == 'completion/complete'
                          for line in (tmp_path / 'log').read_text().splitlines()):
                await asyncio.sleep(0.005)
        queued = asyncio.create_task(manager.complete('peer', 'prompt', 'review', 'topic', 'queued'))
        await asyncio.sleep(0.02)
        await manager.disconnect('peer')
        outcomes = await asyncio.wait_for(asyncio.gather(active, queued, return_exceptions=True), 3)
        assert all(isinstance(error, McpError) for error in outcomes)
        assert not manager._completion_waiters
        frames = [json.loads(line) for line in (tmp_path / 'log').read_text().splitlines()]
        assert len([frame for frame in frames if frame['method'] == 'completion/complete']) == 1
    finally:
        await manager.aclose()


@pytest.mark.parametrize('mutate', [
    {'kind': []}, {'argument': ''}, {'value': 'x' * 8193}, {'context': [['language', True]]},
    {'context': [['language', 'python'], ['language', 'rust']]}, {'values': [None]}, {'has_more': 1}, {'total': -1}])
def test_completion_ipc_codec_rejects_malformed_context_scope_and_counts(mutate):
    from xdog.agent.mcp.completion import McpCompletion
    value = json.loads(json.dumps(asdict(McpCompletion('peer', 'prompt', 'review', 'topic', '', (), ('first',), 1))))
    with pytest.raises(McpError):
        decode_payload({**value, **mutate})


async def test_completion_deadline_bounds_catalog_and_post_as_one_owned_operation(tmp_path):
    manager = McpManager({'peer': {'command': sys.executable, 'timeout': 0.4,
        'args': [str(FIXTURE), 'slow-completions', str(tmp_path / 'log')]}}, tmp_path)
    try:
        await manager.connect('peer')
        with pytest.raises(McpError, match='completion timed out'):
            await manager.complete('peer', 'prompt', 'review', 'topic', 'prefix')
        assert not manager._completion_waiters
        frames = [json.loads(line) for line in (tmp_path / 'log').read_text().splitlines()]
        assert sum(frame['method'] == 'completion/complete' for frame in frames) == 1
    finally:
        await manager.aclose()


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


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('completion_only', [False, True])
async def test_http_opaque_reference_sends_one_completion_without_following_uri(
        tmp_path, http_peer, legacy, completion_only):
    url, state = http_peer
    state['legacy'] = legacy
    state['completion_only'] = completion_only
    manager = McpManager({'peer': {'transport': 'http', 'url': url}}, tmp_path)
    try:
        await manager.connect('peer')
        result = await manager.complete('peer', 'resource', 'file:///not-opened/private', 'opaque-field', 'pre')
        assert result.kind == 'resource' and result.value == 'pre'
        assert len([frame for frame in state['requests'] if frame['method'] == 'completion/complete']) == 1
        assert not any(frame['method'].startswith(('resources/', 'prompts/')) for frame in state['requests'])
        if not legacy:
            assert state['gets'] == 0
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


@pytest.mark.parametrize('legacy', [False, True])
async def test_completion_only_http_metadata_requires_an_object_capability(tmp_path, http_peer, legacy):
    url, state = http_peer
    state.update(legacy=legacy, completion_only=True, invalid_completion=True)
    manager = McpManager({'peer': {'transport': 'http', 'url': url}}, tmp_path)
    try:
        with pytest.raises(McpError, match='supported capability'):
            await manager.connect('peer')
        assert manager.status()[0]['state'] == 'disconnected' and not manager.tools()
        assert not any(frame['method'] == 'completion/complete' for frame in state['requests'])
    finally:
        await manager.aclose()
