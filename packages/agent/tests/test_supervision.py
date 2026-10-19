from __future__ import annotations

import asyncio
import threading
from dataclasses import replace

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.agent.supervision import MAX_TRANSCRIPT_BYTES, ChildSupervisor
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall, Usage
from xdog.ai.utils.event_stream import EventStream


def done_stream(message, seen=None):
    def stream(model, context, options):
        if seen is not None:
            seen.append(options.max_tokens)
        async def generate():
            yield DoneEvent(message=message)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)
    return stream


def factory(spec, publish):
    return Agent(done_stream(AssistantMessage(content=(TextContent(text="answer"),))),
                 config=AgentConfig(model="test/dummy"))


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.002)


async def test_start_inspect_wait_close_persists_once_and_emits_owned_status():
    saved, events = [], []
    owner = ChildSupervisor(factory, saved.append)
    owner.publish = events.append
    try:
        identifier = await owner.start("Investigate")
        result = await owner.control("wait", identifier)
        assert result["status"] == "completed" and result["result"] == "answer"
        assert result["calls"] == 1 and result["access"] == "read-only"
        assert result["messages"][0]["role"] == "user"
        assert len(saved) == 1 and not saved[0]["settling"] and not owner.active
        assert events and all(event["child_id"] == identifier for event in events)
        await owner.control("close", identifier)
        assert owner.snapshot() == {"children": []}
        with pytest.raises(ValueError, match="another supervisor"):
            await owner.control("inspect", identifier)
        assert len(saved) == 1
    finally:
        await owner.aclose()
    with pytest.raises(RuntimeError, match="closed"):
        await owner.start("again")
    assert not owner._thread.is_alive()


@pytest.mark.parametrize("kwargs", [
    {"task": ""}, {"task": "x" * 16001}, {"task": "x", "access": "all"},
    {"task": "x", "access": []},
    {"task": "x", "inherit_context": 1},
])
async def test_invalid_start_does_not_create_owner(kwargs):
    owner = ChildSupervisor(factory)
    with pytest.raises(ValueError):
        await owner.start(**kwargs)
    assert owner._thread is None
    await owner.aclose()


async def test_persistence_holds_slot_and_canceled_control_until_settlement():
    entered, release = threading.Event(), threading.Event()
    saved = []
    def persist(record):
        entered.set()
        assert release.wait(5)
        saved.append(record)
    owner = ChildSupervisor(factory, persist)
    try:
        ids = [await owner.start(str(i)) for i in range(3)]
        await until(entered.is_set)
        assert owner.active
        ids.append(await owner.start("fourth"))
        operation = asyncio.create_task(owner.control("cancel", ids[0]))
        await asyncio.sleep(0.02)
        operation.cancel()
        await asyncio.sleep(0.02)
        operation.cancel()
        await asyncio.sleep(0.02)
        assert not operation.done() and owner.active
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        await until(lambda: not owner.active)
        assert len(saved) == 4
    finally:
        release.set()
        await owner.aclose()


async def test_close_rejects_new_work_while_persistence_drains():
    entered, release = threading.Event(), threading.Event()
    def persist(record):
        entered.set()
        assert release.wait(5)
    owner = ChildSupervisor(factory, persist)
    await owner.start("task")
    await until(entered.is_set)
    closing = asyncio.create_task(owner.aclose())
    try:
        await asyncio.sleep(0.02)
        with pytest.raises(RuntimeError, match="closed"):
            await owner.start("new")
        assert not closing.done()
    finally:
        release.set()
        await closing
    assert not owner._thread.is_alive()


async def test_catalog_exceeds_old_record_limit_and_close_remains_explicit():
    owner = ChildSupervisor(factory)
    try:
        identifiers = []
        for _ in range(20):
            identifier = await owner.start("task")
            await owner.control("wait", identifier)
            identifiers.append(identifier)
        assert len(owner.snapshot()["children"]) == 20
        await owner.control("close", identifiers[0])
        assert len(owner.snapshot()["children"]) == 19
    finally:
        await owner.aclose()


@pytest.mark.parametrize("name", ["agent_spawn", "agent_control", "update_plan"])
async def test_children_reject_recursive_or_parent_plan_tools(name):
    def unsafe(spec, publish):
        agent = factory(spec, publish)
        agent.set_tools([AgentTool(name=name)])
        return agent
    owner = ChildSupervisor(unsafe)
    try:
        identifier = await owner.start("task")
        result = await owner.control("wait", identifier)
        assert result["status"] == "failed" and "parent plan" in result["error"]
        assert result["calls"] == 0
    finally:
        await owner.aclose()


async def test_model_calls_and_reported_tokens_cross_old_boundaries_without_clamping_output():
    from xdog.ai.types import StreamOptions
    limits = []
    async def execute(*args, **kwargs):
        return AgentToolResult(content=(TextContent(text="small"),))
    def looping(spec, publish):
        def stream(model, context, options):
            limits.append(options.max_tokens)
            message = AssistantMessage(
                content=((ToolCall(id=f"call-{len(limits)}", name="read", arguments={}),)
                         if len(limits) < 25 else (TextContent(text="finished"),)),
                usage=replace(Usage(), total_tokens=32000))
            return done_stream(message)(model, context, options)
        return Agent(stream, config=AgentConfig(model="test/dummy", options=StreamOptions(max_tokens=4096)),
                     tools=[AgentTool(name="read", parameters={"type": "object"}, execute=execute)])
    owner = ChildSupervisor(looping)
    try:
        result = await owner.control("wait", await owner.start("task"))
        assert result["status"] == "completed" and result["calls"] == 25
        assert result["tokens"] == 25 * 32000 and limits == [4096] * 25
    finally:
        await owner.aclose()


async def test_complete_tool_pairs_cross_old_aggregate_text_limit_and_persist(tmp_path):
    from xdog.agent.history import validate_checkpoint
    from xdog.agent.messages import dicts_to_messages
    from xdog.agent.supervision_store import ChildStore
    calls = []
    async def execute(*args, **kwargs):
        calls.append(1)
        return AgentToolResult(content=(TextContent(text="x" * (MAX_TRANSCRIPT_BYTES // 3)),))
    def large(spec, publish):
        def stream(model, context, options):
            msg = AssistantMessage(content=((ToolCall(id=f"call-{len(calls)}", name="read", arguments={}),)
                if len(calls) < 10 else (TextContent(text="done"),)))
            return done_stream(msg)(model, context, options)
        return Agent(stream, config=AgentConfig(model="test/dummy"),
                     tools=[AgentTool(name="read", parameters={"type": "object"}, execute=execute)])
    store = ChildStore(tmp_path)
    owner = ChildSupervisor(large, store.save, begin=store.begin, persist_admission=store.save_admission)
    try:
        result = await owner.control("wait", await owner.start("task"))
        assert result["status"] == "completed" and len(calls) == 10
        loaded = store.load()[0]
        assert loaded["messages"] == result["messages"]
        assert len(str(loaded["messages"])) > MAX_TRANSCRIPT_BYTES
        typed = dicts_to_messages(loaded["messages"])
        validate_checkpoint(typed, len(typed) - 1)
    finally:
        await owner.aclose()


async def test_persistence_failure_is_visible_and_callback_failure_does_not_strand_child():
    def persist(record):
        raise OSError("disk full")
    def publish(event):
        raise OSError("disconnected")
    owner = ChildSupervisor(factory, persist)
    owner.publish = publish
    try:
        result = await owner.control("wait", await owner.start("task"))
        assert result["status"] == "failed" and "persistence failed" in result["error"]
        assert not owner.active
    finally:
        await owner.aclose()


async def test_steering_follow_up_and_explicit_cancel_use_real_agent():
    agents = []
    def blocking(spec, publish):
        def stream(model, context, options):
            async def generate():
                await options.cancel.wait()
                yield DoneEvent(message=AssistantMessage(stop_reason="aborted"))
            future = asyncio.get_running_loop().create_future()
            future.set_result(AssistantMessage(stop_reason="aborted"))
            return EventStream.from_async_generator(generate(), result_future=future)
        agent = Agent(stream, config=AgentConfig(model="test/dummy"))
        agents.append(agent)
        return agent
    owner = ChildSupervisor(blocking)
    try:
        identifier = await owner.start("task")
        await until(lambda: agents and agents[0].is_streaming)
        await owner.control("steer", identifier, "new direction")
        await owner.control("follow-up", identifier, "later")
        assert agents[0].has_queued_messages()
        result = await owner.control("cancel", identifier)
        assert result["status"] == "canceled" and not agents[0].is_streaming
        with pytest.raises(ValueError, match="not running"):
            await owner.control("steer", identifier, "late")
    finally:
        await owner.aclose()


async def test_stream_adapter_cannot_change_during_a_turn():
    agent = factory(None, None)
    original = agent.stream_fn
    stream = await agent.prompt("task")
    with pytest.raises(RuntimeError, match="active turn"):
        agent.set_stream_fn(original)
    async for _ in stream:
        pass
    agent.set_stream_fn(original)


async def test_cancel_stops_provider_that_ignores_abort_event():
    entered, stopped = threading.Event(), threading.Event()
    def ignoring(spec, publish):
        def stream(model, context, options):
            async def generate():
                entered.set()
                try:
                    await asyncio.sleep(60)
                    yield DoneEvent(message=AssistantMessage())
                finally:
                    stopped.set()
            future = asyncio.get_running_loop().create_future()
            return EventStream.from_async_generator(generate(), result_future=future)
        return Agent(stream, config=AgentConfig(model="test/dummy"))
    owner = ChildSupervisor(ignoring)
    try:
        identifier = await owner.start("task")
        await until(entered.is_set)
        async with asyncio.timeout(3):
            result = await owner.control("cancel", identifier)
        assert result["status"] == "canceled" and stopped.is_set() and not owner.active
    finally:
        await owner.aclose()


async def test_identified_child_input_queue_is_bounded():
    agents = []
    def waiting(spec, publish):
        def stream(model, context, options):
            async def generate():
                await asyncio.sleep(60)
                yield DoneEvent(message=AssistantMessage())
            return EventStream.from_async_generator(generate())
        agent = Agent(stream, config=AgentConfig(model="test/dummy"))
        agents.append(agent)
        return agent
    owner = ChildSupervisor(waiting)
    try:
        identifier = await owner.start("task")
        await until(lambda: agents and agents[0].is_streaming)
        for i in range(32):
            await owner.control("steer", identifier, str(i))
        with pytest.raises(ValueError, match="queue is full"):
            await owner.control("follow-up", identifier, "overflow")
        assert len(agents[0].pending_inputs()) == 32
    finally:
        await owner.aclose()


async def test_tool_proxies_only_control_their_owner_and_validate_defaults():
    specs = []
    def capturing(spec, publish):
        specs.append(spec)
        return factory(spec, publish)
    owner = ChildSupervisor(capturing)
    other = ChildSupervisor(factory)
    try:
        spawn, control = owner.tools()
        result = await spawn.execute("call", {"task": "task", "inherit_context": False})
        identifier = result.content[0].text
        await owner.control("wait", identifier)
        assert specs[0].access == "read-only" and not specs[0].inherit_context
        with pytest.raises(ValueError, match="another supervisor"):
            await other.control("cancel", identifier)
        result = await control.execute("list", {"action": "list"})
        assert identifier in result.content[0].text
    finally:
        await owner.aclose()
        await other.aclose()


async def test_immediate_producer_cancellation_always_releases_idle_state():
    agent = factory(None, None)
    await agent.prompt("task")
    async with asyncio.timeout(3):
        await agent.cancel_and_wait()
        await agent.wait_for_idle()
    assert not agent.is_streaming


async def test_cancel_after_completion_during_persistence_preserves_terminal_status():
    entered, release = threading.Event(), threading.Event()
    saved = []
    def persist(record):
        entered.set()
        assert release.wait(5)
        saved.append(record)
    owner = ChildSupervisor(factory, persist)
    try:
        identifier = await owner.start("task")
        await until(entered.is_set)
        canceling = asyncio.create_task(owner.control("cancel", identifier))
        await asyncio.sleep(0.02)
        release.set()
        result = await canceling
        assert result["status"] == "completed" and saved[0]["status"] == "completed"
    finally:
        release.set()
        await owner.aclose()


async def test_canceled_aclose_settles_and_preserves_cancellation():
    entered, release = threading.Event(), threading.Event()
    def persist(record):
        entered.set()
        assert release.wait(5)
    owner = ChildSupervisor(factory, persist)
    await owner.start("task")
    await until(entered.is_set)
    closing = asyncio.create_task(owner.aclose())
    await asyncio.sleep(0.02)
    closing.cancel()
    await asyncio.sleep(0.02)
    assert not closing.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert not owner._thread.is_alive() and not owner.active


async def test_sync_close_can_be_awaited_and_never_reopens_owner():
    owner = ChildSupervisor(factory)
    await owner.start("task")
    owner.close()
    await owner.aclose()
    assert not owner._thread.is_alive()
    with pytest.raises(RuntimeError, match="closed"):
        await owner.control("inspect", "unknown")


async def test_cancel_settles_every_parallel_tool_before_persistence():
    entered = set()
    stopped = set()
    release = threading.Event()
    saved = []
    async def execute(identifier, *args, **kwargs):
        entered.add(identifier)
        try:
            await asyncio.sleep(60)
        finally:
            # Simulate an owned metadata cleanup that cancellation cannot abandon.
            while not release.is_set():
                try:
                    await asyncio.sleep(0.002)
                except asyncio.CancelledError:
                    continue
            stopped.add(identifier)
        return AgentToolResult()
    def parallel(spec, publish):
        message = AssistantMessage(content=tuple(ToolCall(id=str(i), name="read", arguments={}) for i in range(3)))
        return Agent(done_stream(message), config=AgentConfig(model="test/dummy"),
                     tools=[AgentTool(name="read", parameters={"type": "object"}, execute=execute)])
    def persist(record):
        assert stopped == {"0", "1", "2"}
        saved.append(record)
    owner = ChildSupervisor(parallel, persist)
    try:
        identifier = await owner.start("task")
        await until(lambda: len(entered) == 3)
        canceling = asyncio.create_task(owner.control("cancel", identifier))
        await asyncio.sleep(0.03)
        assert not canceling.done() and not saved and owner.active
        release.set()
        async with asyncio.timeout(3):
            result = await canceling
        assert result["status"] == "canceled" and len(saved) == 1
    finally:
        release.set()
        await owner.aclose()


async def test_inherited_context_rejects_incomplete_tool_batches():
    def incomplete(spec, publish):
        agent = factory(spec, publish)
        agent.append_message(AssistantMessage(content=(ToolCall(id="pending", name="read", arguments={}),)))
        return agent
    owner = ChildSupervisor(incomplete)
    try:
        result = await owner.control("wait", await owner.start("task"))
        assert result["status"] == "failed" and "tool-call/result batch" in result["error"]
        assert result["calls"] == 0 and result["checkpoint"] == -1
    finally:
        await owner.aclose()


async def test_concurrent_start_and_disposal_cannot_schedule_onto_a_stopped_loop():
    for _ in range(20):
        owner = ChildSupervisor(factory)
        async with asyncio.timeout(3):
            outcomes = await asyncio.gather(owner.start("task"), owner.aclose(), return_exceptions=True)
        assert outcomes[1] is None
        if isinstance(outcomes[0], Exception):
            assert isinstance(outcomes[0], RuntimeError)
        if owner._thread is not None:
            assert not owner._thread.is_alive()
        assert not owner.active


async def test_nonserializable_custom_history_fails_without_stranding_slot():
    from xdog.agent.core import CustomAgentMessage
    def invalid(spec, publish):
        agent = factory(spec, publish)
        agent.append_message(CustomAgentMessage(data={"invalid": object()}))
        return agent
    saved = []
    owner = ChildSupervisor(invalid, saved.append)
    try:
        result = await owner.control("wait", await owner.start("task"))
        assert result["status"] == "failed" and "not JSON serializable" in result["error"]
        assert len(saved) == 1 and result["messages"] == [] and not owner.active
    finally:
        await owner.aclose()


async def test_oversized_assistant_cannot_start_its_attached_mutating_tool():
    effects = []
    async def execute(*args, **kwargs):
        effects.append("must not run")
        return AgentToolResult()
    def oversized(spec, publish):
        message = AssistantMessage(content=(TextContent(text="x" * MAX_TRANSCRIPT_BYTES),
                                           ToolCall(id="write", name="write", arguments={})))
        return Agent(done_stream(message), config=AgentConfig(model="test/dummy"),
                     tools=[AgentTool(name="write", parameters={"type": "object"}, execute=execute)])
    owner = ChildSupervisor(oversized)
    try:
        identifier = await owner.start("task", access="inherit")
        record = await owner.control("wait", identifier)
        assert record["status"] == "failed" and "budget" in record["error"] and effects == []
        assert owner._children[identifier].agent is None
    finally:
        await owner.aclose()


async def test_live_tool_progress_snapshots_are_immutable_and_final_results_are_authoritative():
    entered, release = threading.Event(), threading.Event()
    calls = []
    async def execute(identifier, args, cancel=None, on_update=None, **kwargs):
        await on_update(AgentToolResult(content=(TextContent(text="LIVE-FIRST"),)))
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.002)
        await on_update(AgentToolResult(content=(TextContent(text="LIVE-SECOND"),)))
        return AgentToolResult(content=(TextContent(text="FINAL-CANONICAL"),))
    def make(spec, publish):
        def stream(model, context, options):
            calls.append(1)
            message = (AssistantMessage(content=(ToolCall(id="same", name="run", arguments={}),))
                       if context.messages[-1].role == "user" else
                       AssistantMessage(content=(TextContent(text="done"),)))
            return done_stream(message)(model, context, options)
        return Agent(stream, config=AgentConfig(model="test/dummy"),
                     tools=[AgentTool(name="run", parameters={"type": "object"}, execute=execute)])
    saved = []
    owner = ChildSupervisor(make, saved.append)
    try:
        identifier = await owner.start("task")
        await until(entered.is_set)
        captured = owner.capture_source(identifier)
        assert captured.progress[0].text == "LIVE-FIRST" and captured.progress[0].source_id == "m1:p0"
        inspected = await owner.control("inspect", identifier)
        assert inspected["live_tools"][0]["text"] == "LIVE-FIRST"
        release.set()
        result = await owner.control("wait", identifier)
        assert "FINAL-CANONICAL" in str(result["messages"]) and "live_tools" not in result
        assert captured.progress[0].text == "LIVE-FIRST"  # The held snapshot did not change.
        assert "live_tools" not in saved[0] and len(calls) == 2
    finally:
        release.set()
        await owner.aclose()


async def test_progress_retention_clips_explicitly_without_canceling_valid_tool_execution():
    import json
    entered, release = threading.Event(), threading.Event()
    async def execute(identifier, args, cancel=None, on_update=None, **kwargs):
        await on_update(AgentToolResult(content=(TextContent(text="😀" * MAX_TRANSCRIPT_BYTES),)))
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.002)
        return AgentToolResult(content=(TextContent(text="small final"),))
    def make(spec, publish):
        def stream(model, context, options):
            message = (AssistantMessage(content=(ToolCall(id="run", name="run", arguments={}),))
                       if context.messages[-1].role == "user" else
                       AssistantMessage(content=(TextContent(text="done"),)))
            return done_stream(message)(model, context, options)
        return Agent(stream, config=AgentConfig(model="test/dummy"),
                     tools=[AgentTool(name="run", parameters={"type": "object"}, execute=execute)])
    owner = ChildSupervisor(make)
    try:
        identifier = await owner.start("task")
        await until(entered.is_set)
        captured = owner.capture_source(identifier)
        assert captured.status == "running" and captured.progress[0].limited
        assert len(json.dumps([row.to_dict() for row in captured.progress]).encode()) <= MAX_TRANSCRIPT_BYTES
        release.set()
        result = await owner.control("wait", identifier)
        assert result["status"] == "completed" and "small final" in str(result["messages"])
    finally:
        release.set()
        await owner.aclose()


async def test_main_owner_can_run_more_than_old_concurrency_and_catalog_limits_and_aclose_all():
    entered, cleaned = [], []
    def blocking(spec, publish):
        def stream(model, context, options):
            async def generate():
                entered.append(spec.id)
                try:
                    await asyncio.Event().wait()  # Deliberately ignores the abort event.
                    yield DoneEvent(message=AssistantMessage())
                finally:
                    cleaned.append(spec.id)
            return EventStream.from_async_generator(generate())
        return Agent(stream, config=AgentConfig(model="test/dummy"))
    saved = []
    owner = ChildSupervisor(blocking, saved.append)
    try:
        ids = [await owner.start("task", access="inherit", inherit_context=False,
                                 config={"label": "Goal", "instructions": "Work", "tools": []}) for _ in range(20)]
        await until(lambda: len(entered) == 20)
        assert len(owner.snapshot()["children"]) == 20 and owner.active
        inspected = await owner.control("inspect", ids[0])
        assert inspected["status"] == "running" and not inspected["config"]["inherit_context"]
        assert (await owner.control("cancel", ids[0]))["status"] == "canceled"
        await owner.control("close", ids[0])
    finally:
        await owner.aclose()
    assert set(cleaned) == set(ids) and len(saved) == 20 and not owner.active
    assert all(record["status"] == "canceled" for record in saved)
    assert not owner._thread.is_alive()


async def test_advancing_service_clock_beyond_old_120_and_600_seconds_does_not_end_child():
    entered = threading.Event()
    def blocking(spec, publish):
        def stream(model, context, options):
            async def generate():
                entered.set()
                await options.cancel.wait()
                yield DoneEvent(message=AssistantMessage(stop_reason="aborted"))
            return EventStream.from_async_generator(generate())
        return Agent(stream, config=AgentConfig(model="test/dummy"))
    owner = ChildSupervisor(blocking)
    try:
        identifier = await owner.start("long running task")
        await until(entered.is_set)
        async def advance():
            loop = asyncio.get_running_loop()
            original = loop.time
            loop.time = lambda: original() + 601
        await owner._bridge(advance())
        # Let any previously scheduled deadline callbacks execute on the service loop.
        await asyncio.sleep(0.02)
        assert (await owner.control("inspect", identifier))["status"] == "running"
        assert (await owner.control("cancel", identifier))["status"] == "canceled"
    finally:
        await owner.aclose()
