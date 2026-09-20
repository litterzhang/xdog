"""Opt-in verification of Claude's context override, using only a local mock."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest
from xdog.ai import proxy
from xdog.ai.cli_switch import _configure_claude
from xdog.ai.types import AssistantMessage, DoneEvent, Model, TextContent, Usage

pytestmark = pytest.mark.skipif(
    os.environ.get("XDOG_TEST_CLAUDE_CLI") != "1" or shutil.which("claude") is None,
    reason="Opt-in integration test requires Claude Code with --bare support",
)


async def test_claude_reports_configured_context_window(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    model = Model(
        id="copilot/context-fixture", context_window=1_178_000,
        max_prompt_tokens=1_050_000, max_tokens=128_000,
    )

    class Provider:
        def models(self):
            return (model,)

        def model(self, model_id):
            return model if model_id == model.id else None

        async def complete(self, model_id, context, options):
            return AssistantMessage(
                content=(TextContent(text="Context configured"),),
                usage=Usage(input=10, output=3),
            )

        async def stream(self, model_id, context, options):
            yield DoneEvent(message=await self.complete(model_id, context, options))

    async def connection(reader, writer):
        await proxy._handle_connection(reader, writer, Provider(), api_key="test-only")

    server = await asyncio.start_server(connection, "127.0.0.1", 0)
    process = None
    try:
        port = server.sockets[0].getsockname()[1]
        _configure_claude([model], model, f"http://127.0.0.1:{port}", "test-only", True)
        env = os.environ.copy()
        env.update(
            CLAUDE_CONFIG_DIR=str(tmp_path / ".claude"),
            ANTHROPIC_API_KEY="test-only", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
        )
        # Ensure the setting under test comes from the generated file.
        env.pop("CLAUDE_CODE_MAX_CONTEXT_TOKENS", None)
        env.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)
        process = await asyncio.create_subprocess_exec(
            "claude", "--bare", "--settings", str(tmp_path / ".claude" / "settings.json"),
            "--setting-sources", "", "--strict-mcp-config", "--tools", "", "--no-session-persistence",
            "--output-format", "json", "-p", "Reply Context configured.",
            env=env, cwd=tmp_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
        assert process.returncode == 0, (stdout.decode(), stderr.decode())
        result = json.loads(stdout)
        assert result["result"] == "Context configured"
        assert result["modelUsage"][model.id]["contextWindow"] == 1_178_000
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            await process.communicate()
        server.close()
        await server.wait_closed()
