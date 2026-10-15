import asyncio
import json
import threading
from pathlib import Path

from xdog.agent import AgentConfig
from xdog.agent.agent import Agent
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager


async def test_rpc_dispatches_external_input_while_stdin_is_idle(tmp_path, monkeypatch):
    from xdog.coding.modes.rpc import rpc_mode

    manager = SessionManager(sessions_dir=tmp_path / "sessions")
    value = AgentSession(
        agent=Agent(lambda *_: EventStream.empty(AssistantMessage(content=())), config=AgentConfig(model="test")),
        session_data=manager.create_session(model="test", working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(), tool_registry=None, bash=None, working_dir=tmp_path,
    )
    automatic, calls, outputs = threading.Event(), [], []

    class Input:
        count = 0

        def readline(self, size=-1):
            self.count += 1
            if self.count == 1:
                return json.dumps({"type": "prompt", "message": "register observer"}) + "\n"
            assert automatic.wait(3), "External input never woke the idle RPC session"
            return json.dumps({"type": "quit"}) + "\n"

    class Output:
        def write(self, data):
            outputs.extend(json.loads(line) for line in data.splitlines() if line)
            return len(data)

        def flush(self):
            pass

    async def prompt(session, message, emit, *, input_origin="user", **kwargs):
        calls.append(input_origin)
        if input_origin == "user":
            body = json.loads((Path(__file__).resolve().parents[3] / "docs/fixtures/eyes-host-v1.json").read_text())
            session.external_events.inbox.register("fixture-observer", "fixture-route", "Review tests")
            assert session.external_events.inbox.accept(body) == "accepted"
        else:
            assert json.loads(message)["instruction"] == "Review tests"
            automatic.set()
            emit({"type": "message_end", "content": []})

    monkeypatch.setattr(rpc_mode.sys, "stdin", Input())
    monkeypatch.setattr(rpc_mode.sys, "stdout", Output())
    monkeypatch.setattr(rpc_mode, "_handle_prompt", prompt)
    try:
        assert await asyncio.wait_for(rpc_mode.run_rpc_mode(value), 5) == 0
        assert calls == ["user", "external_event"]
        assert any(row["type"] == "external_event_status" for row in outputs)
    finally:
        automatic.set()
        value.dispose()
