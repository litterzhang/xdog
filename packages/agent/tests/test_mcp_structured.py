from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.mcp import McpError, McpManager
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream

FIXTURE = Path(__file__).parent / "fixtures/mcp_schema_peer.py"


def manager(tmp_path, profile="modern-array"):
    return McpManager(
        {"peer": {"command": sys.executable, "args": [str(FIXTURE), profile, str(tmp_path / "log")]}}, tmp_path
    )


def frames(tmp_path):
    return [json.loads(line) for line in (tmp_path / "log").read_text().splitlines()]


@pytest.mark.parametrize(
    "profile,expected",
    [
        ("modern-array", [{"tag": "STRUCTURED-ARRAY-TAIL-987"}]),
        ("modern-null", None),
        ("modern-scalar", False),
        ("modern-any", [False, None, 0, ""]),
        ("legacy", {"tag": "STRUCTURED-OBJECT-TAIL"}),
    ],
)
async def test_real_stdio_typed_structured_results_are_not_omitted_null_conflated_or_hidden(
    tmp_path, profile, expected
):
    client = manager(tmp_path, profile)
    try:
        await client.connect("peer")
        tool = client.tools()[0]
        result = await tool.execute("call", {"value": None})
        assert result.details["structured_content_present"] is True
        assert result.details["structured_content"] == expected
        assert result.content[0].text == "human summary without full data"
        assert result.content[-1].text == "[MCP structured output]\n" + json.dumps(
            expected, ensure_ascii=False, separators=(",", ":")
        )
        before = len(frames(tmp_path))
        for value in ({"value": 3}, {"value": None, "forbidden": False}):
            with pytest.raises(McpError, match="arguments"):
                await tool.execute("bad", value)
        assert len(frames(tmp_path)) == before
        tool.parameters["properties"]["value"] = {"type": "integer"}
        with pytest.raises(McpError):
            await tool.execute("mutated model schema", {"value": 3})
        assert len(frames(tmp_path)) == before
    finally:
        process = client._schemas._process
        await client.aclose()
        assert process is None or process.returncode is not None


@pytest.mark.parametrize("profile", ["modern-missing", "modern-reject", "modern-nan"])
async def test_missing_false_schema_and_nonfinite_outputs_fail_without_replay(tmp_path, profile):
    client = manager(tmp_path, profile)
    try:
        await client.connect("peer")
        with pytest.raises(McpError):
            await client.tools()[0].execute("call", {"value": None})
        assert sum(frame["method"] == "tools/call" for frame in frames(tmp_path)) == 1
    finally:
        await client.aclose()


async def test_actual_agent_nullable_input_uses_custom_validator_before_permission_and_rechecks_mutation(tmp_path):
    client = manager(tmp_path)
    checks, contexts = [], []
    try:
        await client.connect("peer")
        tool = client.tools()[0]

        def stream(model, context, options):
            contexts.append(context)
            if len(contexts) == 1:
                return EventStream.empty(
                    AssistantMessage(
                        content=(ToolCall(id="call", name=tool.name, arguments={"value": None}),), stop_reason="toolUse"
                    )
                )
            return EventStream.empty(AssistantMessage(content=(TextContent(text="done"),)))

        async def before(context, cancel):
            checks.append(dict(context.args))
            context.args["value"] = (
                3  # A trusted preparation hook can alter effective args, but cannot bypass validation.
            )

        agent = Agent(stream, tools=(tool,), config=AgentConfig(model="test/model"), before_tool_call=before)
        events = await agent.prompt("run")
        async for _event in events:
            pass
        assert checks == [{"value": None}]
        assert not any(frame["method"] == "tools/call" for frame in frames(tmp_path))
        assert any(message.role == "toolResult" and message.is_error for message in contexts[-1].messages)
    finally:
        await client.aclose()


@pytest.fixture
def peer():
    spec = importlib.util.spec_from_file_location("independent_schema_peer", FIXTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = {"legacy": False, "choice": "array", "requests": [], "gets": 0}

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
            body = (
                json.dumps(module.response(request, legacy=state["legacy"], choice=state["choice"])).encode()
                if "id" in request
                else b""
            )
            self.send_response(200 if body else 202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
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


@pytest.mark.parametrize("legacy,choice", [(False, "array"), (False, "null"), (False, "scalar"), (True, "object")])
async def test_owned_http_nullable_input_and_typed_output_follow_negotiated_version(tmp_path, peer, legacy, choice):
    url, state = peer
    state.update(legacy=legacy, choice=choice)
    client = McpManager({"peer": {"transport": "http", "url": url}}, tmp_path)
    try:
        await client.connect("peer")
        result = await client.tools()[0].execute("call", {"value": None})
        assert result.details["structured_content_present"] is True
        assert "[MCP structured output]" in result.content[-1].text
        assert sum(frame["method"] == "tools/call" for frame in state["requests"]) == 1
        if not legacy:
            assert state["gets"] == 0
    finally:
        await client.aclose()


async def test_cancel_event_covers_schema_validation_before_remote_call(tmp_path):
    client = manager(tmp_path)
    try:
        await client.connect("peer")
        entered, settled = asyncio.Event(), asyncio.Event()
        caller_loop = asyncio.get_running_loop()
        original = client._schemas.validate

        async def blocked(document, value, *, label):
            caller_loop.call_soon_threadsafe(entered.set)
            try:
                await asyncio.Event().wait()
            finally:
                caller_loop.call_soon_threadsafe(settled.set)

        client._schemas.validate = blocked
        cancel = asyncio.Event()
        task = asyncio.create_task(client.tools()[0].execute("call", {"value": None}, cancel))
        await asyncio.wait_for(entered.wait(), 2)
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert settled.is_set() and not any(frame["method"] == "tools/call" for frame in frames(tmp_path))
        client._schemas.validate = original
    finally:
        await client.aclose()


async def test_agent_abort_during_pre_permission_validation_settles_without_hook_or_execution(tmp_path):
    client = manager(tmp_path)
    try:
        await client.connect("peer")
        entered, settled = asyncio.Event(), asyncio.Event()
        caller_loop = asyncio.get_running_loop()
        checks = []

        async def blocked(document, value, *, label):
            caller_loop.call_soon_threadsafe(entered.set)
            try:
                await asyncio.Event().wait()
            finally:
                caller_loop.call_soon_threadsafe(settled.set)

        client._schemas.validate = blocked
        tool = client.tools()[0]

        def stream(*args):
            return EventStream.empty(
                AssistantMessage(
                    content=(ToolCall(id="call", name=tool.name, arguments={"value": None}),), stop_reason="toolUse"
                )
            )

        async def before(*args):
            checks.append(True)

        agent = Agent(stream, tools=(tool,), config=AgentConfig(model="test/model"), before_tool_call=before)
        events = await agent.prompt("run")

        async def drain():
            return [event async for event in events]

        task = asyncio.create_task(drain())
        await asyncio.wait_for(entered.wait(), 2)
        agent.abort()
        await asyncio.wait_for(task, 2)
        assert settled.is_set() and not checks
        assert not any(frame["method"] == "tools/call" for frame in frames(tmp_path))
    finally:
        await client.aclose()
