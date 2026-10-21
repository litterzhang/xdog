"""A retained child conversation is not a permanently live Agent/runtime."""
from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager

import pytest
from xdog.agent import AgentTool
from xdog.ai.types import Model, UserMessage

from packages.claw.tests.test_child_agents import setup as claw_setup
from packages.coding.tests.test_child_agents import create as coding_setup


@asynccontextmanager
async def parent(tmp_path, host):
    server = None
    if host == "coding":
        session, contexts = coding_setup(tmp_path)
    else:
        contexts = []
        server, _ = await claw_setup(tmp_path, contexts)
        session = server._orchestrator._runtimes["main"].get_or_create_session(allow_reset=False)
    original = session.agent.stream_fn
    class Adapter:
        def resolve_model(self, name):
            return Model(id=name, context_window=128000, max_tokens=4096, supports_tool_calls=True) \
                if name == "test/model" else None

        def __call__(self, model, context, options):
            return original(model, context, options)

    session.agent.set_stream_fn(Adapter())
    try:
        yield session, contexts
    finally:
        if server is None:
            await session.aclose()
        else:
            await server.stop()


async def idle_thread(owner):
    async with asyncio.timeout(5):
        while owner._thread is not None and owner._thread.is_alive():
            await asyncio.sleep(0.005)


@pytest.mark.parametrize("host", ["coding", "claw"])
@pytest.mark.parametrize("action", ["follow-up", "steer"])
async def test_explicit_message_recreates_runtime_with_same_child_history_then_closes_it(tmp_path, host, action):
    async with parent(tmp_path, host) as (session, contexts):
        owner = session.children.runtime
        built = []
        original = owner.factory

        async def capture(spec, publish):
            agent = await original(spec, publish)
            built.append(agent)
            return agent

        owner.factory = capture
        facts = []
        owner.subscribe(facts.append)
        session.agent.append_message(UserMessage(content="PRIVATE-PARENT-HISTORY"))
        child = await owner.start("remember CHILD-MATERIAL")
        first = await owner.control("wait", child)
        assert first["status"] == "completed", first["error"]
        assert built[0].closed and owner._children[child].agent is None
        if host == "coding":
            await idle_thread(owner)
        before = copy.deepcopy(first["messages"])
        accepted = await owner.control(action, child, "continue with NEW-INPUT")
        assert accepted["accepted_input"]["content"] == "continue with NEW-INPUT"
        second = await owner.control("wait", child)
        assert second["status"] == "completed", second["error"]
        assert second["id"] == child and second["messages"][:len(before)] == before
        assert second["calls"] == 2 and len(built) == 2 and all(agent.closed for agent in built)
        assert len(contexts) == 2 and "CHILD-MATERIAL" in str(contexts[-1].messages)
        assert "NEW-INPUT" in str(contexts[-1].messages)
        assert all("PRIVATE-PARENT-HISTORY" not in str(context.messages) for context in contexts)
        assert second["session_state"]["version"] >= 1 and "messages" not in second["session_state"]
        correspondence = [fact for fact in facts if fact.get("type") == "child_message"]
        assert len({fact["message_id"] for fact in correspondence}) == 4
        assert [fact["direction"] for fact in correspondence] == [
            "parent_to_child", "child_to_parent", "parent_to_child", "child_to_parent"]
        if host == "coding":
            await idle_thread(owner)
            assert owner._loop.is_closed()
        else:
            assert session._host_loop.is_running()


@pytest.mark.parametrize("host", ["coding", "claw"])
async def test_saved_completed_child_restores_without_startup_replay(tmp_path, host):
    async with parent(tmp_path, host) as (session, contexts):
        old = session.children
        child = await old.runtime.start("retain ORIGINAL-MATERIAL")
        first = await old.runtime.control("wait", child)
        assert first["status"] == "completed"
        await old.runtime.aclose()
        if host == "coding":
            session._load_children()
        else:
            from xdog.claw.core.persistence.session_preparation import children
            from xdog.claw.core.runtime.child_agents import ClawChildren
            prepared = await asyncio.to_thread(children, session._store, session.meta.session_id)
            session.children = ClawChildren(session, prepared)
        owner = session.children.runtime
        assert len(contexts) == 1 and owner._loop is None
        await owner.control("follow-up", child, "second independent turn")
        result = await owner.control("wait", child)
        assert result["status"] == "completed", result["error"]
        assert len(contexts) == 2 and "ORIGINAL-MATERIAL" in str(contexts[-1].messages)
        assert sum("ORIGINAL-MATERIAL" in str(row) for row in result["messages"] if row["role"] == "user") == 1


@pytest.mark.parametrize("host", ["coding", "claw"])
async def test_new_parent_tool_is_not_implicitly_added_on_child_continuation(tmp_path, host):
    async with parent(tmp_path, host) as (session, contexts):
        child = await session.children.runtime.start("read material")
        first = await session.children.runtime.control("wait", child)
        assert first["status"] == "completed"

        async def effect(*args, **kwargs):
            raise AssertionError("New parent tool must not become a child grant")

        added = AgentTool(name="later_tool", parameters={"type": "object"}, execute=effect)
        session.agent.set_tools((*session.agent.state.tools, added))
        await session.children.runtime.control("follow-up", child, "continue")
        result = await session.children.runtime.control("wait", child)
        assert result["status"] == "completed", result["error"]
        assert all(tool.name != "later_tool" for tool in contexts[-1].tools)
        assert "later_tool" not in result["config"]["tools"]


@pytest.mark.parametrize("host", ["coding", "claw"])
@pytest.mark.parametrize("status", ["interrupted", "failed", "canceled"])
async def test_unknown_or_failed_old_effects_are_never_replayed_by_new_messages(tmp_path, host, status):
    async with parent(tmp_path, host) as (session, contexts):
        owner = session.children.runtime
        child = await owner.start("read material")
        await owner.control("wait", child)
        owner._children[child].status = status
        with pytest.raises(ValueError, match="not running or safely completed"):
            await owner.control("follow-up", child, "not an authorization to replay")
        assert len(contexts) == 1


@pytest.mark.parametrize("host", ["coding", "claw"])
async def test_failed_continuation_journal_write_prevents_model_and_keeps_recovery_input(tmp_path, host):
    async with parent(tmp_path, host) as (session, contexts):
        owner = session.children.runtime
        child = await owner.start("read material")
        await owner.control("wait", child)
        original = owner.persist

        def fail(record):
            raise OSError("disk failed")

        owner.persist = fail
        try:
            with pytest.raises(OSError, match="disk failed"):
                await owner.control("follow-up", child, "unconfirmed new input")
            record = owner.snapshot()["children"][0]
            assert record["status"] == "interrupted" and not owner.active and len(contexts) == 1
            assert owner._children[child].undelivered[0].content == "unconfirmed new input"
        finally:
            owner.persist = original


@pytest.mark.parametrize("host", ["coding", "claw"])
@pytest.mark.parametrize("cold", [False, True])
async def test_cancel_during_continuation_admission_cannot_start_late_runtime(tmp_path, host, cold):
    import threading

    async with parent(tmp_path, host) as (session, contexts):
        owner = session.children.runtime
        child = await owner.start("read material")
        await owner.control("wait", child)
        if cold:
            owner._children[child].task = None  # Actual restored completed records contain no execution task.
        entered, release = threading.Event(), threading.Event()
        original = owner.persist

        def blocked(record):
            entered.set()
            assert release.wait(5)
            original(record)

        owner.persist = blocked
        caller = asyncio.create_task(owner.control("follow-up", child, "new pending message"))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            await owner.control("cancel", child)
            release.set()
            with pytest.raises(ValueError, match="cancelled during continuation"):
                await caller
            assert len(contexts) == 1 and not owner.active and owner._children[child].agent is None
            assert owner._children[child].undelivered[0].content == "new pending message"
        finally:
            release.set()
            await asyncio.gather(caller, return_exceptions=True)
            owner.persist = original


@pytest.mark.parametrize("host", ["coding", "claw"])
async def test_revoked_parent_tool_refuses_resume_without_losing_accepted_message(tmp_path, host):
    async with parent(tmp_path, host) as (session, contexts):
        owner = session.children.runtime
        child = await owner.start("read material", config={"tools": ["filesystem"]})
        await owner.control("wait", child)
        session.agent.set_active_tools(["current_time"], expected_revision=session.agent.tool_selection_revision)
        await owner.control("follow-up", child, "accepted but not authorized to run")
        result = await owner.control("wait", child)
        assert result["status"] == "failed" and len(contexts) == 1
        assert result["undelivered_inputs"][0]["content"] == "accepted but not authorized to run"


@pytest.mark.parametrize("host", ["coding", "claw"])
async def test_saved_schema_remains_a_ceiling_when_parent_schema_expands(tmp_path, host):
    from xdog.agent import AgentToolResult
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream

    async with parent(tmp_path, host) as (session, contexts):
        effects = []

        async def effect(call_id, args, *extra, **kwargs):
            effects.append(args["value"])
            return AgentToolResult(content=(TextContent(text="effect"),))

        tool = AgentTool(name="limited", parameters={"type": "object", "properties": {
            "value": {"enum": ["safe"]}}, "required": ["value"], "additionalProperties": False}, execute=effect)
        session.agent.set_tools((*session.agent.state.tools, tool))
        if host == "coding":
            session.permissions.set_mode("allow-all")
        class Adapter:
            def resolve_model(self, name):
                return Model(id=name, context_window=128000, max_tokens=4096, supports_tool_calls=True)

            def __call__(self, model, context, options):
                contexts.append(context)
                if context.messages[-1].role == "user" and "attempt" in str(context.messages[-1].content):
                    message = AssistantMessage(content=(ToolCall(id="effect", name="limited",
                                                                 arguments={"value": "unsafe"}),))
                else:
                    message = AssistantMessage(content=(TextContent(text="done"),))
                return EventStream.empty(message)

        session.agent.set_stream_fn(Adapter())
        owner = session.children.runtime
        child = await owner.start("first turn", access="inherit", config={"tools": ["limited"]})
        first = await owner.control("wait", child)
        assert first["status"] == "completed"
        tool.parameters = {"type": "object", "properties": {"value": {"type": "string"}},
                           "required": ["value"], "additionalProperties": False}
        await owner.control("follow-up", child, "attempt an expanded parent call")
        second = await owner.control("wait", child)
        assert second["status"] == "completed", second["error"]
        assert not effects and any(message.get("is_error") for message in second["messages"])


async def test_new_child_runtime_does_not_inherit_previous_approval_cache(tmp_path):
    async with parent(tmp_path, "coding") as (session, contexts):
        requests = []

        def approve(child, request):
            requests.append(request)
            session.children.resolve(child, request.id, "allow_session" if len(requests) == 1 else "deny")

        session.children.approver = approve
        session.children.approval_alive = lambda: True
        from xdog.ai.types import AssistantMessage, TextContent, ToolCall
        from xdog.ai.utils.event_stream import EventStream
        class Adapter:
            calls = 0

            def resolve_model(self, name):
                return Model(id=name, context_window=128000, max_tokens=4096, supports_tool_calls=True)

            def __call__(self, model, context, options):
                self.calls += 1
                if context.messages[-1].role == "user":
                    message = AssistantMessage(content=(ToolCall(id=f"write-{self.calls}", name="filesystem",
                        arguments={"action": "write", "path": "target", "content": "child change"}),))
                else:
                    message = AssistantMessage(content=(TextContent(text="done"),))
                return EventStream.empty(message)

        session.agent.set_stream_fn(Adapter())
        owner = session.children.runtime
        child = await owner.start("write first", access="inherit")
        first = await owner.control("wait", child)
        assert first["status"] == "completed" and (tmp_path / "target").exists()
        (tmp_path / "target").unlink()
        await owner.control("follow-up", child, "write again")
        second = await owner.control("wait", child)
        assert second["status"] == "completed" and len(requests) == 2
        assert not (tmp_path / "target").exists()


@pytest.mark.parametrize("host", ["coding", "claw"])
async def test_compaction_state_and_archive_generation_continue_across_fresh_runtimes(tmp_path, host):
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream

    async with parent(tmp_path, host) as (session, contexts):
        workspace = tmp_path if host == "coding" else tmp_path / "workspace"
        (workspace / "material").write_text("Important finding and reference. " * 400)
        class Adapter:
            calls = 0
            summaries = 0

            def resolve_model(self, name):
                return Model(id=name, context_window=14000, max_prompt_tokens=12000,
                             max_tokens=128, supports_tool_calls=True)

            def __call__(self, model, context, options):
                if not context.tools:
                    self.summaries += 1
                    message = AssistantMessage(content=(TextContent(text="Finding retained with reference"),))
                else:
                    self.calls += 1
                    if self.calls % 26 == 0:
                        message = AssistantMessage(content=(TextContent(text="phase finished"),))
                    else:
                        message = AssistantMessage(content=(ToolCall(id=f"read-{self.calls}", name="filesystem",
                            arguments={"action": "read", "path": "material"}),))
                return EventStream.empty(message)

        adapter = Adapter()
        session.agent.set_stream_fn(adapter)
        owner = session.children.runtime
        child = await owner.start("phase one", config={"tools": ["filesystem"], "model": "test/model"})
        first = await owner.control("wait", child)
        assert first["status"] == "completed", first["error"]
        generation = first["session_state"]["compaction"]["generation"]
        assert generation > 0
        await owner.control("follow-up", child, "phase two")
        second = await owner.control("wait", child)
        assert second["status"] == "completed", second["error"]
        archives = session.children.store.compaction_archives(child)
        assert second["session_state"]["compaction"]["generation"] > generation
        assert [row["generation"] for row in archives] == list(range(1, adapter.summaries + 1))
        assert adapter.calls == 52


@pytest.mark.parametrize("host", ["coding", "claw"])
@pytest.mark.parametrize("text", ["", "   ", "x" * 16001, "中" * 5334])
async def test_malformed_continuation_does_not_write_or_strand_runtime(tmp_path, host, text):
    async with parent(tmp_path, host) as (session, contexts):
        owner = session.children.runtime
        child = await owner.start("first turn")
        await owner.control("wait", child)
        before = session.children.store.load()
        with pytest.raises(ValueError):
            await owner.control("follow-up", child, text)
        assert session.children.store.load() == before
        assert not owner.active and not owner._children[child].queue_work and len(contexts) == 1
        assert owner._children[child].status == "completed"


@pytest.mark.parametrize("host", ["coding", "claw"])
async def test_recovery_encoding_failure_happens_before_publishing_admission_flags(tmp_path, host):
    from unittest.mock import patch

    async with parent(tmp_path, host) as (session, contexts):
        owner = session.children.runtime
        child = await owner.start("first turn")
        await owner.control("wait", child)
        with patch("xdog.agent.child_queue.recovery_to_dicts", side_effect=ValueError("recovery capacity")):
            with pytest.raises(ValueError, match="recovery capacity"):
                await owner.control("follow-up", child, "not accepted")
        assert not owner.active and not owner._children[child].queue_work and len(contexts) == 1
        assert owner._children[child].status == "completed" and not owner._children[child].undelivered


@pytest.mark.parametrize("host", ["coding", "claw"])
@pytest.mark.parametrize("attempt_new_path", [False, True])
async def test_legacy_readonly_continuation_advertises_valid_schema_without_expanding_authority(
    tmp_path, host, attempt_new_path,
):
    from dataclasses import replace

    from jsonschema import Draft202012Validator
    from xdog.ai.protocols.openai_responses import _convert_tools
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.core.messages import messages_to_dicts

    original_path = tmp_path / "CLAUDE.md"
    original_path.write_text("original material")
    new_path = tmp_path / "not-delegated.md"
    new_path.write_text("not part of the old child's proven authority")
    async with parent(tmp_path, host) as (session, contexts):
        filesystem = next(tool for tool in session.agent.state.tools if tool.name == "filesystem")
        executed = []

        async def execute(call_id, args, *extra, **kwargs):
            executed.append(copy.deepcopy(args))
            return await filesystem.execute(call_id, args, *extra, **kwargs)

        session.agent.set_tools(tuple(replace(tool, execute=execute) if tool is filesystem else tool
                                      for tool in session.agent.state.tools))
        phase = "initial"
        checked_wire_schemas = []

        class Adapter:
            def resolve_model(self, name):
                return Model(id=name, context_window=128000, max_tokens=4096, supports_tool_calls=True)

            def __call__(self, model, context, options):
                contexts.append(context)
                if phase == "continued":
                    tools = _convert_tools(tuple(context.tools))
                    schema = next(tool["parameters"] for tool in tools if tool["name"] == "filesystem")
                    # Exercise the actual provider-facing payload, not just the SDK's permissive argument checker.
                    assert schema["type"] == "object" and "properties" in schema
                    assert "enum" not in schema  # no object-valued enum in a function declaration
                    assert schema["properties"]["path"]["enum"] == [str(original_path)]
                    Draft202012Validator.check_schema(schema)
                    checked_wire_schemas.append(copy.deepcopy(schema))
                if context.messages[-1].role == "user" and phase == "initial":
                    response = AssistantMessage(content=(ToolCall(id="original-read", name="filesystem",
                        arguments={"action": "read", "path": str(original_path)}),))
                elif context.messages[-1].role == "user" and attempt_new_path:
                    response = AssistantMessage(content=(ToolCall(
                        id=f"not-authorized-{len(contexts)}", name="filesystem",
                        arguments={"action": "read", "path": str(new_path)}),))
                else:
                    response = AssistantMessage(content=(TextContent(text="confirmed receipt"),))
                return EventStream.empty(response)

        session.agent.set_stream_fn(Adapter())
        old = session.children
        identifier = await old.runtime.start("summarize CLAUDE.md", config={"tools": ["filesystem"], "skills": []})
        first = await old.runtime.control("wait", identifier)
        assert first["status"] == "completed", first["error"]
        assert len(executed) == 1
        # Write a real old-format completed record, then reconstruct the actual host owner.
        first["messages"] = messages_to_dicts(list(old.runtime._children[identifier].transcript))
        await old.runtime.aclose()
        first.pop("session_state")
        first.pop("tool_schemas")
        await asyncio.to_thread(old.store.save, first)
        if host == "coding":
            session._load_children()
        else:
            from xdog.claw.core.persistence.session_preparation import children
            from xdog.claw.core.runtime.child_agents import ClawChildren
            prepared = await asyncio.to_thread(children, session._store, session.meta.session_id)
            session.children = ClawChildren(session, prepared)
        owner = session.children.runtime
        calls_before = len(contexts)
        phase = "continued"
        assert len(contexts) == calls_before  # loading records alone never calls the model
        await owner.control("follow-up", identifier, "confirm receipt of this new follow-up")
        result = await owner.control("wait", identifier)
        assert result["status"] == "completed", result["error"]
        assert checked_wire_schemas and result["result"] == "confirmed receipt"
        assert len(executed) == 1  # no replay, and a new readonly path still cannot execute
        assert result["id"] == identifier
        ceiling = owner.delegation_schemas(identifier)["filesystem"]
        assert ceiling == {"type": "object", "enum": [{"action": "read", "path": str(original_path)}]}
        if attempt_new_path:
            assert any(message.get("is_error") for message in result["messages"])
        # A second fresh runtime must keep the exact same authority, not the projection's approximation.
        await owner.control("follow-up", identifier, "confirm receipt again")
        again = await owner.control("wait", identifier)
        assert again["status"] == "completed", again["error"]
        assert len(executed) == 1 and owner.delegation_schemas(identifier)["filesystem"] == ceiling
