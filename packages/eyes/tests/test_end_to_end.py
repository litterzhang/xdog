import asyncio
import time

from xdog.agent import AgentConfig
from xdog.agent.agent import Agent
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.external_events.messages import serialize
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager


async def test_file_to_normal_coding_turn_then_self_edit_budget_pause(tmp_path, observer_data):
    from xdog.eyes.control import request
    from xdog.eyes.loader import parse
    from xdog.eyes.runtime import ObserverRuntime, definition_data
    from xdog.eyes.transport.protocol import load_context

    observer_data["sources"][0].update(include=["src/**"], poll_interval_ms=50)
    observer_data["detectors"][0].update(debounce_ms=50, max_batch_delay_ms=200)
    (tmp_path / "src").mkdir()
    changed = tmp_path / "src" / "a.py"
    turns = []

    def model(_, context, options):
        turns.append(context.messages)
        changed.write_text("agent edit")
        return EventStream.empty(AssistantMessage(content=()))

    manager = SessionManager(sessions_dir=tmp_path / "sessions")
    session = AgentSession(agent=Agent(model, config=AgentConfig(model="test")),
                           session_data=manager.create_session(model="test", working_dir=tmp_path),
                           session_manager=manager, settings=SettingsManager(), working_dir=tmp_path,
                           bash=None, tool_registry=None)
    binding = session.enable_external_events()
    binding.inbox.human_input()
    context = load_context(binding.host.descriptor)
    runtime = ObserverRuntime(context)
    task = asyncio.create_task(runtime.serve())
    try:
        deadline = time.monotonic() + 5
        while not (context.directory / "eyes.sock").exists():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        await request(context, "start", {"definition": definition_data(parse(observer_data, base_dir=tmp_path))})
        while not all(entry.state == "watching" for entry in runtime.entries.values()):
            assert time.monotonic() < deadline
            await asyncio.sleep(0.02)
        changed.write_text("human edit")
        while binding.inbox.snapshot()["pending_count"] == 0:
            assert time.monotonic() < deadline
            await asyncio.sleep(0.02)
        job = binding.inbox.take(busy=False, human_pending=False)
        await session.send_message(serialize(job), input_origin="external_event")
        binding.inbox.complete(job.id, status="succeeded")
        while binding.inbox.snapshot()["pending_count"] == 0:
            assert time.monotonic() < deadline
            await asyncio.sleep(0.02)
        assert len(turns) == 1
        assert binding.inbox.take(busy=False, human_pending=False) is None
        assert binding.inbox.snapshot()["paused_reason"] == "wake budget exhausted"
        assert changed.read_text() == "agent edit"
        identifier = next(iter(runtime.entries))
        session.permissions.set_mode("deny")
        response = await request(context, "resume", {"registration_id": identifier})
        assert response["status"] == "rejected"
        assert binding.inbox.take(busy=False, human_pending=False) is None
        session.permissions.set_mode("allow-all")
        response = await request(context, "resume", {"registration_id": identifier})
        assert response["status"] == "accepted"
        assert binding.inbox.take(busy=False, human_pending=False) is not None
        assert any(row.type == "xdog.external_event.v1" for row in session.messages if hasattr(row, "type"))
    finally:
        runtime.stop.set()
        await task
        session.dispose()
