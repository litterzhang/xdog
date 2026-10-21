"""Coding retains product ownership around the bounded, versioned SDK runtime."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.agent.events import (
    CompactionEndEvent,
    CompactionFailedEvent,
    CompactionStartEvent,
    SnapshotEvent,
    UsageEvent,
)
from xdog.agent.messages import messages_to_dicts
from xdog.agent.skills import SkillManager
from xdog.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    Model,
    StartEvent,
    StreamOptions,
    TextContent,
    TextDeltaEvent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
    system_prompt_text,
)
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager


class OfflineStream:
    """Synchronous production bursts exceed Agent's default 256-event capacity."""

    def __init__(self) -> None:
        self.contexts: list[Context] = []
        self.responses: list[AssistantMessage] = []
        self.deltas = 0

    def resolve_model(self, _name: str) -> Model:
        return Model(id="offline/model", context_window=200_000, supports_tool_calls=True)

    def __call__(self, _model: str, context: Context, _options: StreamOptions) -> EventStream[AssistantMessage]:
        self.contexts.append(context)
        message = self.responses.pop(0) if self.responses else AssistantMessage(
            content=(TextContent(text="answer"),), usage=Usage(input=10, output=4, total_tokens=14),
        )
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)

        async def events():
            yield StartEvent(partial=message)
            for _ in range(self.deltas):
                yield TextDeltaEvent(index=0, delta="x", partial=message)
            yield DoneEvent(message=message)

        return EventStream.from_async_generator(events(), result_future=future)


@pytest.fixture
async def owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CODING_DIR", str(tmp_path / "coding"))
    # All model access is through the injected adapter, never an ambient provider.
    import xdog.ai as ai
    forbidden = Mock(side_effect=AssertionError("ambient provider access"))
    monkeypatch.setattr(ai, "load", forbidden)
    monkeypatch.setattr(ai, "provider", forbidden)
    # Real installed capabilities may select packaged defaults (e.g. goal). Keep
    # their trusted skill identities available for versioned dump/restore too.
    manager = SkillManager(tmp_path / "skills")
    for slug, scope, body in (("guard", "session", "SESSION-BODY-ONCE"), ("task", "turn", "TURN-BODY-ONCE")):
        directory = tmp_path / "skills" / slug
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: test skill\nmetadata:\n  scope: {scope}\n---\n{body}\n"
        )
    monkeypatch.setattr("xdog.coding.core.slash_commands.skill_manager", lambda: manager)
    stream = OfflineStream()
    store = SessionManager(tmp_path / "sessions")
    session = AgentSession(
        agent=Agent(stream, config=AgentConfig(model="offline/model", supports_tool_calls=True), skills=manager),
        session_data=store.create_session(model="offline/model", working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=tmp_path / "settings.json", project_dir=tmp_path),
        working_dir=tmp_path, tool_registry=None, bash=None, permissions=PermissionManager("ask"),
    )
    try:
        yield session, stream
    finally:
        await session.agent.aclose()
        await session.children.runtime.aclose()
        await session.extensions.aclose()
        session.dispose()
        forbidden.assert_not_called()


def _message_text(context: Context) -> str:
    return "\n".join(message.content if isinstance(message.content, str) else "\n".join(
        part.text for part in message.content if isinstance(part, TextContent)
    ) for message in context.messages)


async def test_two_turns_drain_bounded_events_keep_skills_and_product_persistence(owner, monkeypatch):
    session, stream = owner
    stream.deltas = 600
    session.activate_skill("guard")
    session.activate_skill("task")
    agent = session.agent
    close = AsyncMock(wraps=agent.aclose)
    monkeypatch.setattr(agent, "aclose", close)
    maybe_compact = AsyncMock(wraps=session._maybe_compact)
    monkeypatch.setattr(session, "_maybe_compact", maybe_compact)
    dump = Mock(wraps=agent.dump)
    monkeypatch.setattr(agent, "dump", dump)
    observed: list[Any] = []
    unsubscribe = agent.subscribe(observed.append)
    try:
        for prompt in ("first", "second"):
            result = await asyncio.wait_for(session.send_message(prompt), 5)
            assert result is not None and result.content[0].text == "answer"
        assert session.agent is agent and not agent.closed
        close.assert_not_awaited()
        assert maybe_compact.await_count == 2  # Product pre-turn policy still runs.
        assert len(stream.contexts) == 2  # No implicit SDK summary requests.
        assert len([event for event in observed if event.type == "message_update"]) == 1200
        for context in stream.contexts:
            assert (system_prompt_text(context.system_prompt) or "").count("SESSION-BODY-ONCE") == 1
            assert "TURN-BODY-ONCE" not in (system_prompt_text(context.system_prompt) or "")
        assert _message_text(stream.contexts[0]).count("TURN-BODY-ONCE") == 1
        assert "TURN-BODY-ONCE" not in _message_text(stream.contexts[1])
        # Explicit selections retire turn skills independently of live defaults.
        assert session._active_skills == frozenset({"guard"})
        assert session.active_skills == frozenset({"guard"}) | session.capabilities.default_skills
        assert session.agent.active_skills == tuple(sorted(session.active_skills))
        loaded = session.session_manager.load_session(session.session_id)
        assert loaded is not None
        assert messages_to_dicts(loaded.messages) == messages_to_dicts(session.messages)
        assert len(loaded.messages) == 4
        dump.assert_not_called()  # Product session JSON is not replaced by SDK snapshots.
        assert agent.dump()["format"] == "xdog.agent.snapshot"
        run_ids = {event.run_id for event in observed}
        assert len(run_ids) == 2 and "" not in run_ids
        for run_id in run_ids:
            events = [event for event in observed if event.run_id == run_id]
            assert all(event.session_id for event in events)
            assert [event.sequence for event in events] == sorted({event.sequence for event in events})
    finally:
        unsubscribe()


async def test_sdk_usage_and_compaction_observations_do_not_replace_coding_history(owner):
    session, _ = owner
    session.agent.replace_messages([UserMessage(content="product history")])
    session._persist()
    before = session.session_manager._session_path(session.session_id).read_bytes()
    metadata = {"session_id": "sdk-session", "run_id": "sdk-run", "sequence": 7}
    for event in (
        SnapshotEvent(snapshot_json='{"messages":[{"content":"private snapshot"}]}', **metadata),
        UsageEvent(usage=Usage(input=999), **metadata),
        CompactionStartEvent(**metadata),
        CompactionEndEvent(summary="not a product persistence command", generation=8,
                           removed_messages=(UserMessage(content="archive payload"),), **metadata),
        CompactionFailedEvent(error="diagnostic only", **metadata),
    ):
        session._on_agent_event(event)
    assert session.session_manager._session_path(session.session_id).read_bytes() == before
    assert session.messages == [UserMessage(content="product history")]
    assert session.session_data.compaction is None


async def test_coding_manual_compaction_keeps_recovery_branch_and_does_not_call_sdk_compact(owner, monkeypatch):
    session, stream = owner
    original = [message for i in range(6) for message in (
        UserMessage(content=f"old task {i} " + "x" * 2000), AssistantMessage(content=(TextContent(text="old answer"),)),
    )]
    session.agent.replace_messages(original)
    session._persist()
    sdk_compact = AsyncMock(side_effect=AssertionError("product must retain its compaction transaction"))
    monkeypatch.setattr(session.agent, "compact", sdk_compact)
    stream.responses = [AssistantMessage(content=(TextContent(text="product summary"),))]
    await asyncio.wait_for(session.compact("Retain the task"), 5)
    assert len(stream.contexts) == 1 and not stream.contexts[0].tools
    sdk_compact.assert_not_awaited()
    assert len(session.messages) < len(original)
    metadata = session.session_data.compaction
    assert metadata is not None
    recovery = next(branch for branch in session.session_data.branches
                    if branch["branch_id"] == metadata["recovery_branch_id"])
    assert recovery["messages"] == messages_to_dicts(original)
    persisted = session.session_manager.load_session(session.session_id)
    assert persisted is not None and persisted.compaction == metadata
    assert persisted.branches == session.session_data.branches
    assert not session.agent.closed


async def test_versioned_sdk_restore_does_not_bypass_coding_tool_permission_hook(owner):
    session, stream = owner
    executed: list[str] = []

    async def execute(_id, args, *_args, **_kwargs):
        executed.append(args["action"])
        return AgentToolResult(content=(TextContent(text="read result"),))

    # Path-aware host classification needs a real workspace-relative target;
    # an action-only call is unknown scope, not a valid read-only fixture.
    session.agent.set_tools([*session.agent.state.tools, AgentTool(name="filesystem", execute=execute, parameters={
        "type": "object", "properties": {"action": {"type": "string"}, "path": {"type": "string"}},
        "required": ["action", "path"],
    })])
    for turn in range(2):
        stream.responses = [AssistantMessage(content=(
            ToolCall(id=f"read-{turn}", name="filesystem", arguments={"action": "read", "path": "target.txt"}),
            ToolCall(id=f"write-{turn}", name="filesystem", arguments={"action": "write", "path": "target.txt"}),
        )), AssistantMessage(content=(TextContent(text="finished"),))]
        await asyncio.wait_for(session.send_message("inspect; mutation requires approval"), 5)
        if turn == 0:
            before_restore = messages_to_dicts(session.messages)
            snapshot = session.agent.dump()
            assert snapshot["format"] == "xdog.agent.snapshot"
            assert snapshot["version"] == 1
            assert {skill["slug"] for skill in snapshot["skills"]} == session.active_skills
            session.agent.restore(snapshot)
            assert messages_to_dicts(session.messages) == before_restore
    assert executed == ["read", "read"]
    denied = [message for message in session.messages if isinstance(message, ToolResultMessage)
              and message.tool_call_id.startswith("write-")]
    assert len(denied) == 2 and all(message.is_error for message in denied)
    assert {message.tool_call_id for message in denied} == {"write-0", "write-1"}
    assert all(any(isinstance(part, TextContent) and "requires approval" in part.text
                   for part in message.content) for message in denied)
    assert session.permissions.mode == "ask" and not session.permissions._pending
