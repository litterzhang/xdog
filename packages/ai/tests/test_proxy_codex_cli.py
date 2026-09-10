"""Opt-in real-client regression: XDOG_TEST_CODEX_CLI=1 pytest -k codex_cli.

Only the client is real: it runs read-only in a temporary workspace, using an
isolated CODEX_HOME and a loopback mock provider. No upstream model calls or
shell commands are executed. One case exercises the client's read-only list_agents
tool to verify namespace dispatch and replay. Covers standard and Lite requests.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import shutil
import signal
from pathlib import Path
from typing import Any

import pytest
from xdog.ai import proxy
from xdog.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    Model,
    TextContent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingDoneEvent,
    ThinkingStartEvent,
    ToolCall,
    ToolResultMessage,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("XDOG_TEST_CODEX_CLI") != "1" or shutil.which("codex") is None,
    reason="Opt-in integration test requires the Codex CLI",
)


@pytest.mark.parametrize("model,lite,dispatch,opaque_ids", [
    ("gpt-5.4", False, False, False), ("gpt-5.6-sol", True, False, False),
    ("gpt-5.6-sol", True, True, False), ("gpt-6-astra", True, True, True),
])
async def test_codex_cli_accepts_proxy_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, model: str, lite: bool, dispatch: bool, opaque_ids: bool,
) -> None:
    contexts: list[Context] = []
    requests: list[dict[str, Any]] = []
    original_read = proxy._read_http_request
    reasoning_id = "opaque+/signed-fixture==" if opaque_ids else "rs_signed_fixture"
    encrypted = hmac.new(b"test-only-key", reasoning_id.encode(), hashlib.sha256).hexdigest()

    async def read(reader: asyncio.StreamReader) -> Any:
        request = await original_read(reader)
        if request and request[0] == "POST":
            requests.append(json.loads(request[3]))
        return request

    monkeypatch.setattr(proxy, "_read_http_request", read)

    class FixtureProvider:
        def models(self) -> tuple[Model, ...]:
            return (Model(id=model),)

        async def stream(self, model_id: str, context: Context, options: Any) -> Any:
            contexts.append(context)
            if dispatch and len(contexts) == 1:
                assert context.tools
                tool = next(t for t in context.tools if "Tool collaboration.list_agents." in t.description)
                thinking = ThinkingContent(thinking="Check the agent list", thinking_signature=json.dumps({
                    "type": "reasoning", "id": reasoning_id, "encrypted_content": encrypted,
                    "summary": [{"type": "summary_text", "text": "Check the agent list"}],
                }))
                # Exercise late metadata too: the opaque signature and original
                # ID are only supplied in the block-done snapshot.
                provisional = AssistantMessage(content=(ThinkingContent(thinking_signature=json.dumps({
                    "type": "reasoning", "id": "provisional+/fixture==", "summary": [],
                    "encrypted_content": "provisional-ciphertext",
                })),)) if opaque_ids else None
                yield ThinkingStartEvent(partial=provisional)
                yield ThinkingDeltaEvent(delta="Check the agent list")
                yield ThinkingDoneEvent(thinking=thinking.thinking, partial=AssistantMessage(content=(thinking,)))
                yield DoneEvent(message=AssistantMessage(
                    content=(thinking, ToolCall(id="call_probe", name=tool.name, arguments={})), stop_reason="toolUse",
                ))
                return
            if dispatch:
                replayed = next(item for item in requests[-1]["input"] if item.get("type") == "reasoning")
                assert replayed["id"].startswith("rs_") and replayed["encrypted_content"] == encrypted
                # The client sees a prefixed ID; the provider must see the exact
                # original ID paired with the signed ciphertext after decoding.
                native = next(
                    json.loads(part.thinking_signature)
                    for message in context.messages if isinstance(message, AssistantMessage)
                    for part in message.content if isinstance(part, ThinkingContent) and part.thinking_signature
                )
                assert native["id"] == reasoning_id and native["encrypted_content"] == encrypted
            yield DoneEvent(message=AssistantMessage(content=(TextContent(text="Proxy integration OK"),)))

    provider = FixtureProvider()

    async def connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await proxy._handle_connection(reader, writer, provider)

    server = await asyncio.start_server(connection, "127.0.0.1", 0)
    process = None
    try:
        port = server.sockets[0].getsockname()[1]
        env = os.environ.copy()
        env.update(CODEX_HOME=str(tmp_path), NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        env.pop("OPENAI_API_KEY", None)
        env.pop("CODEX_API_KEY", None)
        # The friendly name enables OpenAI-specific namespace serialization in
        # Lite mode, but the base URL and all authentication remain local/fake.
        name = "OpenAI" if lite else "Offline fixture"
        config = (f'model_providers.fixture={{name="{name}",base_url="http://127.0.0.1:{port}/v1",'
                  'wire_api="responses",requires_openai_auth=false}')
        prompt = (
            "List agents in this isolated test, then reply Proxy integration OK. Do not run shell commands."
            if dispatch else "Reply with Proxy integration OK. Do not use tools."
        )
        process = await asyncio.create_subprocess_exec(
            "codex", "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
            "--sandbox", "read-only", "--json", "-c", 'model_provider="fixture"', "-c", config,
            "-c", 'web_search="disabled"', "--model", model, prompt,
            cwd=tmp_path, env=env, start_new_session=True,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 60)
        assert process.returncode == 0, (stdout.decode(), stderr.decode())
        assert b"Proxy integration OK" in stdout
        assert len(contexts) == len(requests) == (2 if dispatch else 1) and contexts[0].tools
        if dispatch:
            result = contexts[-1].messages[-1]
            assert isinstance(result, ToolResultMessage) and result.tool_call_id == "call_probe"
            assert isinstance(result.content[0], TextContent)
            assert isinstance(json.loads(result.content[0].text)["agents"], list)
        request = requests[0]
        if lite:
            additional = next(item for item in request["input"] if item.get("type") == "additional_tools")
            groups = [tool for tool in additional["tools"] if tool["type"] == "namespace"]
            assert groups
            assert {tool["type"] for group in groups for tool in group["tools"]} >= {"function", "custom"}
        else:
            assert {tool["type"] for tool in request["tools"]} >= {"function", "custom", "tool_search"}
    finally:
        if process is not None and process.returncode is None:
            os.killpg(process.pid, signal.SIGTERM)
            await process.communicate()
        server.close()
        await server.wait_closed()
