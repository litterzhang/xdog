"""A retained child conversation is not a permanently live Agent/runtime."""
from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager

import pytest
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
