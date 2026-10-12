"""Task tool -> live service -> real orchestrator/Agent queue -> fake channel."""
import asyncio
import sys
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.channel import TuiChannel
from xdog.claw.config import ClawConfig
from xdog.claw.core.memory.manager import MemoryManager
from xdog.claw.core.planning.task_scheduler import TaskScheduler
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.tools.tool_task import create_task_tool
from xdog.claw.core.types import Group, GroupConfig, QueueMode, UserInput

NOW = datetime.fromisoformat("2026-10-12T09:20:00+08:00").timestamp()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    clock = [NOW]
    calls = []
    gates = {}
    replies = {}

    def stream_fn(model, context, options=None):
        # All model work is synthetic, including the real Agent tool loop.
        content = context.messages[-1].content
        prompt = content if isinstance(content, str) else "".join(p.text for p in content if isinstance(p, TextContent))
        calls.append(prompt)
        msg = replies.get(prompt, AssistantMessage(content=(TextContent(text="Scheduled report"),)))

        async def events():
            if prompt in gates:
                started, release = gates[prompt]
                started.set()
                # Model doubles honor the same cooperative cancellation contract as providers.
                waits = [asyncio.create_task(release.wait())]
                if options is not None and options.cancel is not None:
                    waits.append(asyncio.create_task(options.cancel.wait()))
                try:
                    await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for wait in waits:
                        wait.cancel()
                    await asyncio.gather(*waits, return_exceptions=True)
            yield StartEvent(partial=msg)
            yield DoneEvent(message=msg)

        future = asyncio.get_running_loop().create_future()
        future.set_result(msg)
        return EventStream.from_async_generator(events(), result_future=future)

    def create(group, data_dir, **kwargs):
        # Keep the actual GroupRuntime/AgentSession, but no embedding or provider discovery.
        return GroupRuntime(group=group, data_dir=data_dir, model="test/dummy", stream_fn=stream_fn,
                            task_scheduler=kwargs["task_scheduler"], send_fn=kwargs["send_fn"])

    monkeypatch.setattr(GroupRuntime, "create", create)
    config = ClawConfig(data_dir=str(tmp_path / "data"), tasks_file=str(tmp_path / "custom" / "tasks.json"),
                        enabled_tools=("task",), max_concurrent_agents=2)
    orch = Orchestrator(config, stream_fn=stream_fn)
    orch.scheduler._clock = lambda: clock[0]
    for gid in ("g1", "g2"):
        orch.register_group(Group(id=gid, config=GroupConfig(debounce_ms=0, queue_mode=QueueMode.STEER)))
    channel = TuiChannel()
    channel.send_message = AsyncMock()
    orch.add_channel(channel)
    return orch, clock, calls, gates, replies, channel


async def create(orch, prompt="report", group="g1", **schedule):
    runtime = orch._runtimes[group]
    ctx = runtime.get_or_create_session().agent.tool_context
    assert ctx["_task_scheduler"] is orch.scheduler
    result = await create_task_tool().execute(
        "tool-call", {"action": "schedule", "prompt": prompt, **schedule}, ctx=ctx,
    )
    assert "Task scheduled:" in result.content[0].text
    return orch.scheduler.list_tasks()[-1]


async def settle(orch):
    await asyncio.wait_for(asyncio.gather(*list(orch._scheduled_runs.values())), timeout=3)


async def test_future_tool_task_runs_once_reports_and_reloads(setup):
    orch, clock, calls, _, _, channel = setup
    try:
        task = await create(orch, run_at="2026-10-12T09:24:00+08:00")
        assert orch.scheduler._tasks_file.name == "tasks.json"  # custom config path honored
        assert not (orch._data_dir / "scheduled_tasks.json").exists()
        await orch.tick()
        assert not calls and channel.send_message.await_count == 0
        clock[0] += 239
        await orch.tick()
        assert not calls
        clock[0] += 1
        await orch.tick()
        await orch.tick()  # No duplicate admission on overlapping ticks.
        await settle(orch)
        channel.send_message.assert_awaited_once_with("g1", "Scheduled report")
        assert calls == ["report"]
        saved = orch.scheduler.get_task(task.id)
        assert saved.status == "succeeded" and saved.delivery_status == "delivered"
        assert saved.last_run == clock[0] and not saved.enabled
        restarted = TaskScheduler(orch.scheduler._tasks_file, clock=lambda: clock[0])
        assert restarted.get_task(task.id) == saved
        clock[0] += 365 * 86400
        assert restarted.get_due_tasks() == []
        await orch.tick()
        assert calls == ["report"]
    finally:
        await orch.stop()


async def test_model_can_create_task_via_real_tool_loop(setup):
    orch, clock, calls, _, replies, channel = setup
    replies["schedule now"] = AssistantMessage(content=(ToolCall(id="schedule-1", name="task", arguments={
        "action": "schedule", "prompt": "scheduled follow-up", "run_at": "2026-10-12T09:24:00+08:00",
    }),), stop_reason="toolUse")
    try:
        result = await orch.route_message(UserInput(group_id="g1", content="schedule now"))
        assert result and not result.error
        assert orch.scheduler.list_tasks()[0].prompt == "scheduled follow-up"
        channel.send_message.assert_not_awaited()
        clock[0] += 240
        await orch.tick()
        await settle(orch)
        assert calls[-1] == "scheduled follow-up"
        channel.send_message.assert_awaited_once_with("g1", "Scheduled report")
    finally:
        await orch.stop()


@pytest.mark.parametrize("failure", ["model", "delivery", "no-channel", "unknown-group", "empty"])
async def test_failures_never_mark_success(setup, failure):
    orch, clock, _, _, replies, channel = setup
    try:
        task = await create(orch, run_at="2026-10-12T09:24:00+08:00")
        if failure == "model":
            replies["report"] = AssistantMessage(stop_reason="error", error_message="model failed")
        elif failure == "delivery":
            channel.send_message.side_effect = RuntimeError("delivery failed")
        elif failure == "no-channel":
            orch._channels.clear()
        elif failure == "unknown-group":
            del orch._runtimes["g1"]
        elif failure == "empty":
            replies["report"] = AssistantMessage(content=())
        clock[0] += 240
        await orch.tick()
        await settle(orch)
        saved = orch.scheduler.get_task(task.id)
        assert saved.last_run is None and saved.last_error and saved.status == "failed"
        assert not saved.enabled
        assert saved.delivery_status == ("delivered" if failure == "model" or failure == "unknown-group" else "failed")
        assert TaskScheduler(orch.scheduler._tasks_file).get_task(task.id) == saved
        await orch.tick()
        assert not orch._scheduled_runs
    finally:
        await orch.stop()


@pytest.mark.parametrize("cancel", [False, True])
async def test_busy_group_waits_without_steering_or_losing_callback(setup, cancel):
    orch, clock, calls, gates, _, channel = setup
    started, release = asyncio.Event(), asyncio.Event()
    gates["busy"] = started, release
    user = asyncio.create_task(orch.route_message(UserInput(group_id="g1", content="busy")))
    try:
        await asyncio.wait_for(started.wait(), 3)
        task = await create(orch, run_at="2026-10-12T09:24:00+08:00")
        clock[0] += 240
        await orch.tick()
        # Synchronize on the normal queue admission, not elapsed wall clock.
        admitted = asyncio.Event()
        original_start = orch.scheduler.start_run

        def start_run(tid):
            result = original_start(tid)
            admitted.set()
            return result

        orch.scheduler.start_run = start_run
        await asyncio.wait_for(admitted.wait(), 3)
        assert calls == ["busy"]
        assert orch.scheduler.get_task(task.id).last_run is None
        if cancel:
            ctx = orch._runtimes["g1"].get_or_create_session().agent.tool_context
            await create_task_tool().execute("cancel", {"action": "cancel", "task_id": task.id}, ctx=ctx)
        release.set()
        await asyncio.wait_for(user, 3)
        await settle(orch)
        if cancel:
            assert calls == ["busy"] and orch.scheduler.get_task(task.id) is None
            # Cancellation is not an execution error report.
            channel.send_message.assert_not_awaited()
        else:
            assert calls == ["busy", "report"]
            channel.send_message.assert_awaited_once_with("g1", "Scheduled report")
    finally:
        release.set()
        await user
        await orch.stop()


async def test_slow_task_does_not_block_other_group_or_next_tick(setup):
    orch, clock, calls, gates, _, channel = setup
    started, release = asyncio.Event(), asyncio.Event()
    gates["slow"] = started, release
    try:
        await create(orch, prompt="slow", run_at="2026-10-12T09:24:00+08:00")
        clock[0] += 240
        await asyncio.wait_for(orch.tick(), 1)
        await asyncio.wait_for(started.wait(), 3)
        await create(orch, group="g2", prompt="fast", interval_seconds=1)
        clock[0] += 1
        await asyncio.wait_for(orch.tick(), 1)
        fast = orch.scheduler.list_tasks()[-1]
        await asyncio.wait_for(orch._scheduled_runs[fast.id], 3)
        assert "fast" in calls and not release.is_set()
        assert fast.id not in orch._scheduled_runs or orch._scheduled_runs[fast.id].done()
    finally:
        release.set()
        await settle(orch)
        await orch.stop()


async def test_recurring_failure_advances_instead_of_retrying_same_occurrence(setup):
    orch, clock, calls, _, _, channel = setup
    try:
        task = await create(orch, interval_seconds=60)
        clock[0] += 60
        channel.send_message.side_effect = RuntimeError("offline")
        await orch.tick()
        await settle(orch)
        saved = orch.scheduler.get_task(task.id)
        assert saved.status == "failed" and saved.next_run == clock[0] + 60 and saved.enabled
        await orch.tick()
        assert calls == ["report"]
        channel.send_message.side_effect = None
        clock[0] += 60
        await orch.tick()
        await settle(orch)
        saved = orch.scheduler.get_task(task.id)
        assert saved.status == "succeeded" and saved.last_run == clock[0]
        assert saved.next_run == clock[0] + 60 and calls == ["report", "report"]
    finally:
        await orch.stop()


@pytest.mark.parametrize("custom", [False, True])
def test_data_dir_override_and_custom_tasks_file_have_one_authority(tmp_path, custom):
    configured = tmp_path / "configured"
    override = tmp_path / "override"
    selected = tmp_path / "selected.json"
    config = ClawConfig(data_dir=str(configured), tasks_file=str(selected) if custom else "")
    orch = Orchestrator(config, data_dir=override)
    assert orch.scheduler._tasks_file == (selected if custom else override / "scheduled_tasks.json")


async def test_shutdown_settles_active_scheduled_run_without_success(setup):
    orch, clock, _, gates, _, channel = setup
    started, release = asyncio.Event(), asyncio.Event()
    gates["report"] = started, release
    task = await create(orch, run_at="2026-10-12T09:24:00+08:00")
    clock[0] += 240
    await orch.tick()
    await asyncio.wait_for(started.wait(), 3)
    await asyncio.wait_for(orch.stop(), 3)
    saved = orch.scheduler.get_task(task.id)
    assert saved.status == "failed" and saved.last_run is None and not saved.enabled
    assert "cancelled" in saved.last_error and not orch._scheduled_runs
    channel.send_message.assert_not_awaited()


@pytest.mark.parametrize("backend_failure", [False, True])
async def test_scheduled_memory_search_refreshes_and_reports(setup, monkeypatch, backend_failure):
    """The shared Agent context retains both live services across recurring turns."""
    orch, clock, calls, _, replies, channel = setup
    monkeypatch.setattr("xdog.claw.core.memory.indexer._embedding_model_instance", None)
    for name in ("sentence_transformers", "sqlite_vec", "rank_bm25"):
        monkeypatch.setitem(sys.modules, name, None)
    runtime = orch._runtimes["g1"]
    runtime.workspace_dir.mkdir(parents=True, exist_ok=True)
    memory = MemoryManager(runtime.workspace_dir, runtime.data_dir, "g1")
    runtime.memory = memory
    runtime._enabled_tools = ("task", "memory")
    if backend_failure:
        memory._searcher.search = AsyncMock(side_effect=RuntimeError("backend unavailable"))
    replies["memory report"] = AssistantMessage(content=(ToolCall(id="memory-search", name="memory", arguments={
        "action": "search", "query": "陆知行 xdog-youju",
    }),), stop_reason="toolUse")
    try:
        task = await create(orch, prompt="memory report", interval_seconds=60)
        ctx = runtime.get_or_create_session().agent.tool_context
        assert ctx["_memory_search"].__self__ is memory
        assert ctx["_task_scheduler"] is orch.scheduler
        for revision in ("initialneedle", "updatedneedle"):
            text = f"陆知行 xdog-youju {revision}"
            (runtime.workspace_dir / "MEMORY.md").write_text(text, encoding="utf-8")
            replies[f"[MEMORY.md] {text}"] = AssistantMessage(content=(TextContent(text=text),))
            clock[0] += 60
            await orch.tick()
            await settle(orch)
            assert calls[-2:] == ["memory report", f"[MEMORY.md] {text}"]
            channel.send_message.assert_awaited_with("g1", text)
            saved = orch.scheduler.get_task(task.id)
            assert saved.status == "succeeded" and saved.delivery_status == "delivered"
            assert saved.last_run == clock[0] and saved.next_run == clock[0] + 60
        assert channel.send_message.await_count == 2
        assert TaskScheduler(orch.scheduler._tasks_file).get_task(task.id) == saved
    finally:
        await orch.stop()
        await memory._searcher.close()
