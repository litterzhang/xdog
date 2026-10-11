"""Tests for RPC mode, including approval responses during a running turn."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from xdog.agent import AgentContext, BeforeToolCallContext
from xdog.ai.types import AssistantMessage, ToolCall
from xdog.coding.core.permissions import PermissionManager


def test_rpc_status_command():
    from xdog.coding.modes.rpc.rpc_mode import run_rpc_mode

    assert callable(run_rpc_mode)


def test_rpc_accepts_permission_response_while_prompt_is_running(monkeypatch):
    import xdog.coding.modes.rpc.rpc_mode as rpc_module

    permission_seen = threading.Event()
    result_seen = threading.Event()
    request_id = [""]
    output_lines: list[str] = []

    class Output:
        def write(self, value: str) -> int:
            for line in value.splitlines():
                if not line:
                    continue
                output_lines.append(line)
                event = json.loads(line)
                if event.get("type") == "permission_request":
                    request_id[0] = event["id"]
                    permission_seen.set()
                elif event.get("type") == "test_result":
                    result_seen.set()
            return len(value)

        def flush(self) -> None:
            pass

    class Input:
        def __init__(self) -> None:
            self.index = 0

        def readline(self, size=-1) -> str:
            self.index += 1
            if self.index == 1:
                return json.dumps({"type": "prompt", "message": "run it"}) + "\n"
            if self.index == 2:
                assert permission_seen.wait(2)
                return json.dumps({
                    "type": "permission_response",
                    "id": request_id[0],
                    "decision": "allow_once",
                }) + "\n"
            if self.index == 3:
                assert result_seen.wait(2)
                return json.dumps({"type": "quit"}) + "\n"
            return ""

    class Session:
        def __init__(self) -> None:
            self.permissions = PermissionManager("ask")
            self.session_id = "rpc-test"

        def abort(self) -> None:
            pass

    async def fake_handle_prompt(session: Any, message: str, emit: Any) -> None:
        ctx = BeforeToolCallContext(
            assistant_message=AssistantMessage(),
            tool_call=ToolCall(id="call", name="bash", arguments={"command": "pytest"}),
            args={"command": "pytest"},
            context=AgentContext(),
        )
        result = await session.permissions.before_tool_call(ctx)
        emit({"type": "test_result", "allowed": result is None, "message": message})

    monkeypatch.setattr(rpc_module.sys, "stdin", Input())
    monkeypatch.setattr(rpc_module.sys, "stdout", Output())
    monkeypatch.setattr(rpc_module, "_handle_prompt", fake_handle_prompt)

    exit_code = asyncio.run(rpc_module.run_rpc_mode(Session()))  # type: ignore[arg-type]

    assert exit_code == 0
    events = [json.loads(line) for line in output_lines]
    assert any(event.get("type") == "permission_request" for event in events)
    assert any(event.get("type") == "test_result" and event["allowed"] for event in events)
