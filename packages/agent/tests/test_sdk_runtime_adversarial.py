"""Offline service-runtime adversarial tests; every wait/process has a bound."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult, CompactionConfig
from xdog.agent.compaction import safe_cut_indices
from xdog.agent.events import CompactionEndEvent, CompactionFailedEvent, SnapshotEvent, UsageEvent
from xdog.agent.skills import SkillRepo
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent, ToolCall, Usage, UserMessage
from xdog.ai.utils.event_stream import EventStream


def _config(*, auto=False, snapshots=False, buffer=4):
    return AgentConfig(
        model="offline/adversarial", system_prompt="PRESERVE_HOST_INSTRUCTIONS", event_buffer_size=buffer,
        emit_snapshots=snapshots,
        compaction=CompactionConfig(auto=auto, context_window=5000, output_reserve=128, safety_margin=100),
    )


def _old_history():
    return [UserMessage(content="previous material " * 500),
            AssistantMessage(content=(TextContent(text="previous findings " * 500),)),
            UserMessage(content="CURRENT_TASK_GOAL")]


async def _close(agent):
    # A broken adapter may correctly report incomplete release. Never hide a hang.
    with suppress(RuntimeError):
        await asyncio.wait_for(agent.aclose(), 3)


async def test_auto_compaction_exceeds_100_tool_rounds_without_hidden_cap_or_pair_loss():
    calls, summaries, receipts = [], [], []

    async def execute(call_id, args, cancel, on_update, **kwargs):
        receipts.append(call_id)
        return AgentToolResult(content=(TextContent(text="verified tool material " * 120),))

    def adapter(model, context, options):
        if not context.tools:
            summaries.append(context)
            return EventStream.empty(AssistantMessage(content=(TextContent(text="Prior findings; continue task."),),
                                                     usage=Usage(input=11, output=7)))
        calls.append(context)
        safe_cut_indices(context.messages)
        assert "CURRENT_TASK_GOAL" in str(context.messages)
        assert context.system_prompt == "PRESERVE_HOST_INSTRUCTIONS"
        number = len(calls)
        reply = (AssistantMessage(content=(ToolCall(id=f"round-{number}", name="read"),), stop_reason="toolUse")
                 if number <= 125 else AssistantMessage(content=(TextContent(text="finished 125 rounds"),)))
        return EventStream.empty(reply)

    agent = Agent(adapter, config=_config(auto=True),
                  tools=[AgentTool(name="read", parameters={"type": "object"}, execute=execute)])
    try:
        async with asyncio.timeout(15):
            stream = await agent.prompt("CURRENT_TASK_GOAL")
            events = []
            async for event in stream:
                events.append(event)
                assert stream.buffered_events <= 4
                await asyncio.sleep(0)  # Exercise bounded backpressure throughout the run.
        assert not agent.state.error
        assert len(calls) == 126 and receipts == [f"round-{n}" for n in range(1, 126)]
        ends = [event for event in events if isinstance(event, CompactionEndEvent)]
        assert len(ends) == len(summaries) and len(ends) > 10
        assert [event.generation for event in ends] == list(range(1, len(ends) + 1))
        assert all(event.automatic and event.removed_messages for event in ends)
        assert not any(isinstance(event, CompactionFailedEvent) for event in events)
        usage = [event for event in events if isinstance(event, UsageEvent) and event.source == "compaction"]
        assert len(usage) == len(summaries) and sum(event.usage.output for event in usage) == 7 * len(summaries)
        assert len({event.run_id for event in events}) == 1
        assert [event.sequence for event in events] == sorted({event.sequence for event in events})
        assert agent.dump()["compaction"]["generation"] == len(summaries)
    finally:
        await _close(agent)


async def test_summary_terminal_event_does_not_drain_past_done_before_cleanup():
    released = asyncio.Event()
    reply = AssistantMessage(content=(TextContent(text="valid terminal summary"),), usage=Usage(input=8, output=3))

    def adapter(*args):
        future = asyncio.get_running_loop().create_future()
        async def generate():
            try:
                future.set_result(reply)
                yield DoneEvent(stop_reason="stop", message=reply)
                # A terminal event is authoritative, not permission to keep draining.
                await asyncio.sleep(10)
            finally:
                released.set()
        return EventStream.from_async_generator(generate(), future)

    agent = Agent(adapter, config=_config())
    agent.replace_messages(_old_history())
    try:
        result = await asyncio.wait_for(agent.compact(), 2)
        assert result.summary == "valid terminal summary" and released.is_set()
    finally:
        await _close(agent)
    assert released.is_set()


async def test_summary_cleanup_failure_cannot_claim_fully_closed_runtime():
    closes = []
    class BrokenSummary(EventStream):
        async def aclose(self):
            closes.append(True)
            await super().aclose()
            raise OSError("cannot release summary transport")

    def adapter(*args):
        return BrokenSummary.empty(AssistantMessage(content=(TextContent(text="otherwise valid summary"),),
                                                    usage=Usage(input=13, output=2, total_tokens=15)))

    agent = Agent(adapter, config=_config())
    agent.replace_messages(_old_history())
    before = agent.dump()
    observed = []
    agent.subscribe(observed.append)
    try:
        with pytest.raises(ValueError, match="cleanup"):
            await asyncio.wait_for(agent.compact(), 2)
        assert agent.dump() == before and closes == [True]
        assert any(isinstance(event, CompactionFailedEvent) for event in observed)
        assert any(isinstance(event, UsageEvent) and event.usage.output == 2 for event in observed)
        with pytest.raises(RuntimeError, match="cleanup.*incomplete"):
            await asyncio.wait_for(agent.aclose(), 2)
        assert not agent.closed
    finally:
        await _close(agent)


async def test_failed_summary_usage_with_component_counts_is_not_silently_discarded():
    def adapter(*args):
        return EventStream.empty(AssistantMessage(content=(TextContent(text="incomplete"),), stop_reason="length",
                                                 usage=Usage(input=13, output=2)))
    agent = Agent(adapter, config=_config())
    agent.replace_messages(_old_history())
    observed = []
    agent.subscribe(observed.append)
    try:
        with pytest.raises(ValueError):
            await asyncio.wait_for(agent.compact(), 2)
        assert any(isinstance(event, UsageEvent) and event.source == "compaction"
                   and event.usage.input == 13 and event.usage.output == 2 for event in observed)
    finally:
        await _close(agent)


async def test_close_in_auto_summary_with_abandoned_bounded_consumer_retains_completed_batches():
    entered, released = asyncio.Event(), asyncio.Event()
    signals, requests = [], []
    consumer = None

    async def execute(*args, **kwargs):
        return AgentToolResult(content=(TextContent(text="confirmed tool result " * 170),))

    def adapter(model, context, options):
        if context.tools:
            requests.append(context)
            return EventStream.empty(AssistantMessage(content=(ToolCall(id=str(len(requests)), name="read"),),
                                                     stop_reason="toolUse"))
        signals.append(options.cancel)
        async def hold_summary():
            try:
                entered.set()
                yield StartEvent(partial=AssistantMessage())
                await asyncio.sleep(10)
            finally:
                released.set()
        return EventStream.from_async_generator(hold_summary())

    agent = Agent(adapter, config=_config(auto=True, buffer=1),
                  tools=[AgentTool(name="read", parameters={"type": "object"}, execute=execute)])
    observed = []
    agent.subscribe(observed.append)
    try:
        stream = await agent.prompt("CURRENT_TASK_GOAL")
        consumer = asyncio.create_task(stream.collect())
        await asyncio.wait_for(entered.wait(), 3)
        before = agent.messages
        safe_cut_indices(before)
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        await asyncio.wait_for(agent.aclose(), 2)
        assert agent.closed and released.is_set() and all(signal.is_set() for signal in signals)
        assert agent.messages == before and agent.dump()["compaction"]["generation"] == 0
        assert any(isinstance(event, CompactionFailedEvent) and event.automatic for event in observed)
        assert not any(isinstance(event, CompactionEndEvent) for event in observed)
        remaining = await asyncio.wait_for(stream.collect(), 1)
        assert sum(event.type == "agent_end" for event in remaining) == 1
        assert len(requests) > 1
    finally:
        if consumer is not None:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        await _close(agent)


async def test_active_turn_skill_survives_compacted_snapshot_but_not_a_new_task(tmp_path):
    for slug, scope, body in (("once", "turn", "TURN_GUARD_BODY"), ("stable", "session", "SESSION_GUARD_BODY")):
        directory = tmp_path / slug
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: guard\nmetadata:\n  scope: {scope}\n---\n{body}")
    manager = SkillRepo(builtin=False, packaged={}).register(directory=tmp_path, name="shared")
    main_calls, summaries, receipts = [], [], []
    post_summary_calls = 0

    async def execute(call_id, *args, **kwargs):
        receipts.append(call_id)
        return AgentToolResult(content=(TextContent(text="retained materials and references " * 110),))

    async def adapter(model, context, options):
        nonlocal post_summary_calls
        if not context.tools:
            summaries.append(context)
            return EventStream.empty(AssistantMessage(content=(TextContent(text="Task goal and findings."),)))
        main_calls.append(context)
        assert str(context.messages).count("TURN_GUARD_BODY") == 1
        assert str(context.system_prompt).count("SESSION_GUARD_BODY") == 1
        if summaries:
            post_summary_calls += 1
            if post_summary_calls > 1:
                await asyncio.sleep(10)
        return EventStream.empty(AssistantMessage(content=(ToolCall(id=f"read-{len(main_calls)}", name="read"),),
                                                 stop_reason="toolUse"))

    tools = [AgentTool(name="read", parameters={"type": "object"}, execute=execute)]
    source = Agent(adapter, config=_config(auto=True, snapshots=True, buffer=1), tools=tools,
                   skills=manager, active_skills=["once", "stable"])
    iterator = None
    try:
        stream = await source.prompt("CURRENT_TASK_GOAL")
        iterator = stream.__aiter__()
        saved = None
        async with asyncio.timeout(5):
            async for event in iterator:
                if isinstance(event, SnapshotEvent) and event.snapshot["compaction"]["generation"]:
                    saved = event.snapshot
                    break
        assert saved is not None and saved["run_active"] and not saved["interrupted"]
        assert saved["messages"][-1]["role"] == "toolResult"
        assert "TURN_GUARD_BODY" not in json.dumps(saved["messages"])
        assert next(skill for skill in saved["skills"] if skill["slug"] == "once")["lifecycle"] == "active"
    finally:
        await _close(source)
        if iterator is not None:
            await iterator.aclose()

    receipts_before = receipts[:]
    restored_requests = []
    def finish(model, context, options):
        restored_requests.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="finished resumed task"),)))

    target = Agent(finish, config=_config(), tools=tools, skills=manager)
    try:
        target.restore(saved)
        second_snapshot = target.dump()
        # A second serialization hop must neither consume nor duplicate active skills.
        target.restore(json.loads(json.dumps(second_snapshot)))
        assert not restored_requests and receipts == receipts_before
        await asyncio.wait_for((await target.continue_()).collect(), 3)
        assert target.run_id == saved["run_id"]
        assert str(restored_requests[-1].messages).count("TURN_GUARD_BODY") == 1
        assert str(restored_requests[-1].system_prompt).count("SESSION_GUARD_BODY") == 1
        assert target.dump()["compaction"]["generation"] == saved["compaction"]["generation"]
        await asyncio.wait_for((await target.prompt("new unrelated task")).collect(), 3)
        assert "TURN_GUARD_BODY" not in str(restored_requests[-1].messages)
        assert receipts == receipts_before  # Completed pre-crash tools are never replayed.
    finally:
        await _close(target)

    fresh = Agent(finish, config=_config(), tools=tools, skills=manager)
    try:
        fresh.restore(saved)
        await asyncio.wait_for((await fresh.prompt("start a different run")).collect(), 3)
        assert fresh.run_id != saved["run_id"]
        assert "TURN_GUARD_BODY" not in str(restored_requests[-1].messages)
        assert receipts == receipts_before
    finally:
        await _close(fresh)


_KILL_PROCESS = r"""
import asyncio, json, os, sys
from pathlib import Path
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.agent.events import SnapshotEvent
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream

root, mode = Path(sys.argv[1]), sys.argv[2]
def durable(path, data):
    with path.open('w') as output:
        json.dump(data, output)
        output.flush()
        os.fsync(output.fileno())

async def main():
    requests, effects = [], []
    def adapter(model, context, options):
        requests.append(context)
        if mode == 'worker':
            return EventStream.empty(AssistantMessage(content=(
                ToolCall(id=f'effect-{len(requests)}', name='record'),), stop_reason='toolUse'))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='resume acknowledged'),)))

    async def execute(call_id, *args, **kwargs):
        effects.append(call_id)
        with (root / 'receipts').open('a') as output:
            output.write(call_id + '\n')
            output.flush()
            os.fsync(output.fileno())
        if call_id == 'effect-2':
            # The side effect happened, but no successful tool result was committed.
            durable(root / 'diagnostic.json', agent.dump(recovery=True))
            print(json.dumps({'ready': True, 'pid': os.getpid()}), flush=True)
            await asyncio.sleep(30)
        return AgentToolResult(content=(TextContent(text='confirmed ' + call_id),))

    agent = Agent(adapter, config=AgentConfig(model='offline/kill', emit_snapshots=True, event_buffer_size=2),
                  tools=[AgentTool(name='record', parameters={'type':'object'}, execute=execute)])
    try:
        if mode == 'worker':
            def capture(event):
                if isinstance(event, SnapshotEvent) and event.snapshot['run_active']:
                    durable(root / 'safe.json', event.snapshot)
            agent.subscribe(capture)
            async with asyncio.timeout(35):
                await (await agent.prompt('perform the task')).collect()
            raise AssertionError('worker should have been killed')
        diagnostic = json.loads((root / 'diagnostic.json').read_text())
        before = agent.dump()
        try:
            agent.restore(diagnostic)
        except ValueError as error:
            assert 'reconciliation' in str(error)
        else:
            raise AssertionError('unknown side effect was accepted as resumable')
        assert agent.dump() == before and not requests and not effects
        safe = json.loads((root / 'safe.json').read_text())
        agent.restore(safe)
        assert not requests and not effects
        # The newer effect is absent from the older saved boundary. SDK cannot
        # detect it or promise exactly-once execution from this older snapshot.
        assert 'effect-2' not in json.dumps(agent.dump()['messages'])
        await asyncio.wait_for((await agent.continue_()).collect(), 3)
        assert len(requests) == 1 and not effects
        assert 'effect-1' in str(requests[0].messages) and 'effect-2' not in str(requests[0].messages)
        print(json.dumps({'pid':os.getpid(), 'requests':len(requests), 'replayed':len(effects),
                          'diagnostic_status':diagnostic['interrupted_tools'][0]['status']}), flush=True)
    finally:
        await asyncio.wait_for(agent.aclose(), 3)

asyncio.run(main())
"""


async def _kill_and_reap(process):
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    await asyncio.wait_for(process.communicate(), 5)


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX SIGKILL semantics")
async def test_sigkill_after_effect_receipt_restores_only_saved_boundary_without_tool_replay(tmp_path):
    async def spawn(mode):
        return await asyncio.wait_for(asyncio.create_subprocess_exec(
            sys.executable, "-c", _KILL_PROCESS, str(tmp_path), mode,
            cwd=Path(__file__).resolve().parents[3],
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE), 5)

    worker = await spawn("worker")
    try:
        line = await asyncio.wait_for(worker.stdout.readline(), 8)
        if not line:
            stdout, stderr = await asyncio.wait_for(worker.communicate(), 3)
            pytest.fail(f"Worker exited before receipt: {stdout!r} {stderr!r}")
        ready = json.loads(line)
        assert ready["ready"] and ready["pid"] == worker.pid
        assert (tmp_path / "receipts").read_text().splitlines() == ["effect-1", "effect-2"]
        worker.send_signal(signal.SIGKILL)
        await asyncio.wait_for(worker.communicate(), 5)
        assert worker.returncode == -signal.SIGKILL
    finally:
        await _kill_and_reap(worker)

    diagnostic = json.loads((tmp_path / "diagnostic.json").read_text())
    safe = json.loads((tmp_path / "safe.json").read_text())
    assert diagnostic["interrupted"] and diagnostic["interrupted_tools"][0]["status"] == "unknown"
    assert safe["run_active"] and not safe["interrupted"]
    assert safe["messages"][-1]["role"] == "toolResult"
    receipts_before = (tmp_path / "receipts").read_bytes()
    recovery = await spawn("recover")
    try:
        stdout, stderr = await asyncio.wait_for(recovery.communicate(), 8)
        assert recovery.returncode == 0, stdout.decode() + stderr.decode()
        result = json.loads(stdout)
        assert result["pid"] != worker.pid and result["requests"] == 1 and result["replayed"] == 0
        assert result["diagnostic_status"] == "unknown"
        assert (tmp_path / "receipts").read_bytes() == receipts_before
    finally:
        await _kill_and_reap(recovery)
