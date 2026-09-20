"""Opt-in catalog validation using the installed Codex client, with no LLM calls."""
from __future__ import annotations

import asyncio
import json
import os
import shutil

import pytest
from xdog.ai.cli_switch import configure_codex
from xdog.ai.types import Model

pytestmark = pytest.mark.skipif(
    os.environ.get("XDOG_TEST_CODEX_CLI") != "1" or shutil.which("codex") is None,
    reason="Opt-in integration test requires the Codex CLI",
)


async def test_codex_loads_exported_catalog(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(root))
    model = Model(
        id="copilot/catalog-fixture", name="Catalog Fixture", context_window=1_178_000,
        max_prompt_tokens=1_050_000, max_tokens=128_000, reasoning=True,
        supported_efforts=("low", "medium", "high"),
    )
    configure_codex(model, [model], "http://127.0.0.1:1", "test-only")
    process = await asyncio.create_subprocess_exec(
        "codex", "app-server", cwd=tmp_path,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin and process.stdout and process.stderr

    async def request(payload):
        process.stdin.write((json.dumps(payload) + "\n").encode())
        await process.stdin.drain()
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), 15)
            assert line, (await process.stderr.read()).decode()
            response = json.loads(line)
            if response.get("id") == payload["id"]:
                assert "error" not in response, response
                return response["result"]

    try:
        await request({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "xdog-test", "version": "1"},
        }})
        result = await request({"id": 2, "method": "model/list", "params": {}})
        row = next(row for row in result["data"] if row["model"] == model.id)
        assert row["displayName"] == "Catalog Fixture"
        assert row["defaultReasoningEffort"] == "medium"
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.communicate(), 10)


async def test_switched_codex_authenticates_to_proxy(tmp_path, monkeypatch):
    from pathlib import Path

    from xdog.ai import proxy
    from xdog.ai.types import AssistantMessage, DoneEvent, TextContent

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    model = Model(
        id="copilot/fixture", context_window=200_000, max_tokens=16_000, supports_web_search=False,
    )
    received = []
    tool_types = []
    read_request = proxy._read_http_request

    async def capture_request(reader):
        request = await read_request(reader)
        if request and request[0] == "POST":
            tool_types.extend(t.get("type") for t in json.loads(request[3]).get("tools", []))
        return request

    monkeypatch.setattr(proxy, "_read_http_request", capture_request)

    class Provider:
        def models(self):
            return (model,)

        async def stream(self, model_id, context, options):
            received.append(model_id)
            yield DoneEvent(message=AssistantMessage(content=(TextContent(text="Authenticated proxy OK"),)))

    async def connection(reader, writer):
        await proxy._handle_connection(reader, writer, Provider(), api_key="fixture-proxy-secret")

    server = await asyncio.start_server(connection, "127.0.0.1", 0)
    process = None
    try:
        port = server.sockets[0].getsockname()[1]
        configure_codex(model, [model], f"http://127.0.0.1:{port}", "fixture-proxy-secret")
        process = await asyncio.create_subprocess_exec(
            "codex", "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
            "Reply Authenticated proxy OK. Do not use tools.", cwd=tmp_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
        assert process.returncode == 0, (tool_types, stdout.decode(), stderr.decode())
        assert b"Authenticated proxy OK" in stdout
        assert received == [model.id]
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            await process.communicate()
        server.close()
        await server.wait_closed()
