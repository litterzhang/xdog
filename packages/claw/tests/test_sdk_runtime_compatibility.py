"""Claw's cached Agent, transcript and memory pipeline survive SDK additions."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from xdog.agent import AgentTool, AgentToolResult, MessageEndEvent
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
from xdog.claw.core.persistence.transcript_convert import transcript_to_messages
from xdog.claw.core.runtime.display_events import AssistantTextDelta
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.types import Group, UserInput


class OfflineStream:
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
    import xdog.ai as ai
    forbidden = Mock(side_effect=AssertionError("ambient provider access"))
    monkeypatch.setattr(ai, "load", forbidden)
    monkeypatch.setattr(ai, "provider", forbidden)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "MEMORY.md").write_text("DURABLE-MEMORY-MARKER")
    manager = SkillManager(tmp_path / "skills", packaged={})
    for slug, scope, body in (("guard", "session", "SESSION-BODY-ONCE"), ("task", "turn", "TURN-BODY-ONCE")):
        directory = tmp_path / "skills" / slug
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: test skill\nmetadata:\n  scope: {scope}\n---\n{body}\n"
        )
    stream = OfflineStream()
    runtime = GroupRuntime(group=Group(id="compat"), data_dir=tmp_path / "data", workspace_dir=workspace,
                           model="offline/model", stream_fn=stream, skill_manager=manager)
    runtime._tools = []  # Explicit host tool set, no installed integrations or real services.
    session = runtime.get_or_create_session(allow_reset=False)
    try:
        yield session, runtime, stream
    finally:
        await session.agent.aclose()
        await session.children.runtime.aclose()
        await session.extensions.aclose()
        session.dispose()
        runtime.mcp.close()
        forbidden.assert_not_called()


def _message_text(context: Context) -> str:
    return "\n".join(message.content if isinstance(message.content, str) else "\n".join(
        part.text for part in message.content if isinstance(part, TextContent)
    ) for message in context.messages)


async def test_cached_agent_survives_bounded_turns_with_single_skill_injection_and_jsonl(owner, monkeypatch):
    session, runtime, stream = owner
    await session.skills_control("load", "guard")
    await session.skills_control("load", "task")
    stream.deltas = 600
    agent = session.agent
    close = AsyncMock(wraps=agent.aclose)
    monkeypatch.setattr(agent, "aclose", close)
    compact = AsyncMock(wraps=session._maybe_compact)
    monkeypatch.setattr(session, "_maybe_compact", compact)
    dump = Mock(wraps=agent.dump)
    monkeypatch.setattr(agent, "dump", dump)
    displays: list[Any] = []
    deliveries = AsyncMock()
    observed: list[Any] = []
    unsubscribe = agent.subscribe(observed.append)
    try:
        for prompt in ("first", "second"):
            result = await asyncio.wait_for(session.run_turn(
                UserInput(group_id="compat", content=prompt, sender="test"),
                on_display_event=displays.append, on_assistant_message=deliveries,
            ), 5)
            assert result.error is None and result.response_text == "answer"
            assert result.usage == {"input": 10, "output": 4, "cache_read": 0, "cache_write": 0}
            assert runtime.get_or_create_session(allow_reset=False) is session
            assert session.agent is agent and not agent.closed
        close.assert_not_awaited()  # Cached runtime must never close its Agent per turn.
        assert compact.await_count == 2
        assert len(stream.contexts) == 2  # No hidden SDK automatic summary call.
        assert len([event for event in displays if isinstance(event, AssistantTextDelta)]) == 1200
        assert [call.args[0] for call in deliveries.await_args_list] == ["answer", "answer"]
        for context in stream.contexts:
            prefix = system_prompt_text(context.system_prompt) or ""
            assert prefix.count("SESSION-BODY-ONCE") == 1
            assert prefix.count("DURABLE-MEMORY-MARKER") == 1
            assert "TURN-BODY-ONCE" not in prefix
        assert _message_text(stream.contexts[0]).count("TURN-BODY-ONCE") == 1
        assert "TURN-BODY-ONCE" not in _message_text(stream.contexts[1])
        assert runtime.transcript_store.get_active_skills(session.meta.session_id) == ("guard",)
        stored = runtime.transcript_store.load_transcript(session.meta.session_id)
        assert len(stored) == 4
        restored = messages_to_dicts(transcript_to_messages(stored))
        current = messages_to_dicts(list(agent.messages))
        # Claw owns write timestamps and its four-counter usage envelope; calling
        # messages_to_transcript twice deliberately produces new timestamps.
        assert [(row["role"], row["content"]) for row in restored] == [
            (row["role"], row["content"]) for row in current
        ]
        assert stored[1]["usage"] == stored[3]["usage"] == {
            "input": 10, "output": 4, "cache_read": 0, "cache_write": 0,
        }
        dump.assert_not_called()  # Claw keeps its own transcript format, not SDK snapshot persistence.
        assert agent.dump()["format"] == "xdog.agent.snapshot"
        assert len({event.run_id for event in observed}) == 2
        assert all(event.run_id and event.session_id and event.sequence > 0 for event in observed)
    finally:
        unsubscribe()


async def test_new_sdk_observations_do_not_duplicate_usage_delivery_or_compact_claw(owner):
    session, runtime, _ = owner
    original = (UserMessage(content="existing conversation"),)
    session.agent.replace_messages(original)
    session._persist()
    before = runtime.transcript_store.load_transcript(session.meta.session_id)
    metadata = {"session_id": "sdk-session", "run_id": "sdk-run", "sequence": 3}
    usage = Usage(input=10, output=4, total_tokens=14)

    async def events():
        yield SnapshotEvent(snapshot_json='{"messages":[{"content":"private snapshot"}]}', **metadata)
        yield UsageEvent(usage=usage, **metadata)
        yield CompactionStartEvent(**metadata)
        yield CompactionEndEvent(summary="sdk summary is not channel output", generation=1,
                                 removed_messages=original, **metadata)
        yield UsageEvent(usage=Usage(input=800, output=30, total_tokens=830), source="compaction", **metadata)
        yield CompactionFailedEvent(error="diagnostic only", **metadata)
        yield MessageEndEvent(message=AssistantMessage(content=(TextContent(text="answer"),), usage=usage), **metadata)

    deliver = AsyncMock()
    display = Mock()
    result = await session._drain_events(events(), on_display_event=display, on_assistant_message=deliver)
    assert result.usage == {"input": 10, "output": 4, "cache_read": 0, "cache_write": 0}
    deliver.assert_awaited_once_with("answer")
    display.assert_not_called()
    assert session.agent.messages == original
    assert runtime.transcript_store.load_transcript(session.meta.session_id) == before
    assert session._compaction_metadata is None


async def test_claw_manual_compaction_keeps_memory_flush_archive_and_transcript_checkpoint(owner, monkeypatch):
    session, runtime, stream = owner
    session._restore_messages()
    original = tuple(message for i in range(6) for message in (
        UserMessage(content=f"old task {i} " + "x" * 2000), AssistantMessage(content=(TextContent(text="old answer"),)),
    ))
    session.agent.replace_messages(original)
    session._persist()
    order: list[str] = []

    async def flush(messages, _prompt, _tools):
        assert tuple(messages) == original
        order.append("memory flush")
        (runtime.workspace_dir / "MEMORY.md").write_text("FLUSHED-DURABLE-MEMORY")

    async def summarize(messages, _previous, **_kwargs):
        assert tuple(messages) == original
        assert (runtime.workspace_dir / "MEMORY.md").read_text() == "FLUSHED-DURABLE-MEMORY"
        order.append("summary")
        return "Product-owned summary"

    async def reindex(path, name):
        assert path.name == name and "old task 0" in path.read_text()
        order.append("archive index")

    runtime.flush_runner = SimpleNamespace(run=AsyncMock(side_effect=flush))
    runtime.summarizer = SimpleNamespace(summarize=AsyncMock(side_effect=summarize))
    runtime.memory = SimpleNamespace(search=AsyncMock(), reindex_fn=AsyncMock(side_effect=reindex))
    sdk_compact = AsyncMock(side_effect=AssertionError("Claw owns its memory/compaction pipeline"))
    monkeypatch.setattr(session.agent, "compact", sdk_compact)
    result = await asyncio.wait_for(session.compact("Keep durable memory"), 5)
    assert result["changed"] is True
    assert order == ["memory flush", "summary", "archive index"]
    sdk_compact.assert_not_awaited()
    assert not stream.contexts  # The explicitly supplied product summarizer was used.
    assert len(session.agent.messages) < len(original)
    stored = runtime.transcript_store.load_transcript(session.meta.session_id)
    assert stored[0]["is_compaction"] is True
    assert stored[0]["compaction"]["recovery_branch_id"] == result["recovery_branch_id"]
    assert result["recovery_branch_id"] in runtime.transcript_store.list_branches(session.meta.session_id)
    assert runtime.get_or_create_session(allow_reset=False) is session and not session.agent.closed


async def test_versioned_sdk_restore_keeps_claw_read_only_access_gate(owner):
    session, runtime, stream = owner
    executed: list[str] = []

    async def execute(_id, args, *_args, **_kwargs):
        executed.append(args["action"])
        return AgentToolResult(content=(TextContent(text="read result"),))

    tool = AgentTool(name="filesystem", execute=execute, parameters={
        "type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"],
    })
    runtime._tools = [tool]
    session.agent.set_tools([*session.agent.state.tools, tool])
    session.access.set_read_only(True)
    for turn in range(2):
        stream.responses = [AssistantMessage(content=(
            ToolCall(id=f"read-{turn}", name="filesystem", arguments={"action": "read"}),
            ToolCall(id=f"write-{turn}", name="filesystem", arguments={"action": "write"}),
        )), AssistantMessage(content=(TextContent(text="finished"),))]
        result = await asyncio.wait_for(session.run_turn(
            UserInput(group_id="compat", content="review", sender="test"),
        ), 5)
        assert result.error is None
        if turn == 0:
            snapshot = session.agent.dump()
            assert snapshot["format"] == "xdog.agent.snapshot"
            session.agent.restore(snapshot)
    assert executed == ["read", "read"]
    blocked = [message for message in session.agent.messages if isinstance(message, ToolResultMessage)
               and message.tool_call_id.startswith("write-")]
    assert len(blocked) == 2 and all(message.is_error for message in blocked)
    assert session.access.state.read_only and not session.agent.closed
