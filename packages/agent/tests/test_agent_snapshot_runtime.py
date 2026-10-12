"""Versioned public Agent restore: fresh processes and unconfirmed side effects."""

from __future__ import annotations

import asyncio
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.agent.skills import SkillManager
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, UserMessage
from xdog.ai.utils.event_stream import EventStream


def plain_agent(**kwargs):
    return Agent(
        lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="offline/test"), **kwargs
    )


async def test_unconfirmed_effect_is_exported_for_diagnosis_not_automatically_replayed():
    started = asyncio.Event()
    effects = []

    async def execute(*args, **kwargs):
        effects.append("external write")
        started.set()
        await asyncio.Event().wait()
        return AgentToolResult()

    tool = AgentTool(name="write", parameters={"type": "object"}, execute=execute)
    agent = Agent(
        lambda *args: EventStream.empty(
            AssistantMessage(content=(ToolCall(id="effect-1", name="write", arguments={}),), stop_reason="toolUse")
        ),
        config=AgentConfig(model="offline/test"),
        tools=[tool],
    )
    await agent.prompt("do work")
    await asyncio.wait_for(started.wait(), 2)
    with pytest.raises(RuntimeError, match="safe snapshot"):
        agent.dump()
    in_flight = agent.dump(recovery=True)
    assert in_flight["interrupted_tools"][0]["status"] == "unknown"
    await asyncio.wait_for(agent.aclose(), 2)
    saved = agent.dump(recovery=True)
    target = plain_agent(tools=[tool])
    before = target.dump()
    with pytest.raises(ValueError, match="reconciliation"):
        target.restore(saved)
    assert target.dump() == before
    assert effects == ["external write"]
    await target.aclose()


def test_restore_validation_is_atomic_and_model_allowlist_is_host_owned():
    source = plain_agent()
    source.replace_messages([UserMessage(content="saved")])
    saved = source.dump()
    target = plain_agent()
    target.append_message(UserMessage(content="keep"))
    before = target.dump()
    broken = copy.deepcopy(saved)
    broken["options"]["temperature"] = "bad"
    with pytest.raises(ValueError):
        target.restore(broken)
    assert target.dump() == before
    with pytest.raises(ValueError, match="not allowed"):
        target.restore(saved, allowed_models=["offline/other"])
    assert target.dump() == before
    target.restore(saved, allowed_models=["offline/test"])
    assert target.messages == source.messages


def test_skill_restore_recomposes_prefix_once_and_rejects_changed_body(tmp_path):
    directory = tmp_path / "guard"
    directory.mkdir()
    skill_file = directory / "SKILL.md"
    skill_file.write_text("---\nname: guard\ndescription: test\n---\nGUARD_BODY")
    manager = SkillManager(tmp_path, packaged={})
    source = plain_agent(skills=manager, active_skills=["guard"])
    source.set_system_prompt("RAW_BASE")
    saved = source.dump()
    for _ in range(3):
        target = plain_agent(skills=manager, active_skills=["guard"])
        target.restore(saved)
        assert str(target.state.system_prompt).count("GUARD_BODY") == 1
        assert target.dump()["base_system_prompt"] == "RAW_BASE"
        saved = target.dump()
    skill_file.write_text("---\nname: guard\ndescription: test\n---\nCHANGED_BODY")
    # Dump fingerprints what was used, not new file contents at snapshot time.
    assert source.dump()["skills"] == saved["skills"]
    target = plain_agent(skills=manager)
    before = target.dump()
    with pytest.raises(ValueError, match="changed host binding"):
        target.restore(saved)
    assert target.dump() == before


_PROCESS = r"""
import asyncio, json, sys
from pathlib import Path
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult, CompactionConfig
from xdog.agent.skills import SkillManager
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream

async def main():
    root, phase = Path(sys.argv[1]), sys.argv[2]
    calls, summaries = [], []
    async def execute(call_id, args, cancel, on_update, **kwargs):
        with (root / 'effects').open('a') as output:
            output.write(phase + '\n')
        return AgentToolResult(content=(TextContent(text='material reference and findings ' * 180),))
    def stream(model, context, options):
        if not context.tools:
            summaries.append(context)
            return EventStream.empty(AssistantMessage(
                content=(TextContent(text='Saved goal, findings and materials'),)))
        calls.append(context)
        assert 'FLOW_HOST_BASE' in str(context.system_prompt)
        if len(calls) == 1:
            return EventStream.empty(AssistantMessage(content=(ToolCall(id=phase, name='record', arguments={}),),
                                                     stop_reason='toolUse'))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='phase complete'),)))
    manager = SkillManager(root / 'skills')
    agent = Agent(stream, config=AgentConfig(model='offline/process', system_prompt='FLOW_HOST_BASE',
                  compaction=CompactionConfig(context_window=30000, output_reserve=256)),
                  tools=[AgentTool(name='record', parameters={'type':'object'}, execute=execute)],
                  skills=manager, active_skills=['flow-workflows', 'once'] if phase == 'A' else [])
    if phase != 'A':
        agent.restore(json.loads((root / 'snapshot.json').read_text()))
        assert not calls and not summaries
        assert 'once' not in agent.active_skills
        assert agent.active_skills == ('flow-workflows',)
    await (await agent.prompt({'A':'init task', 'B':'build task', 'C':'feedback and repair task'}[phase])).collect()
    if phase == 'A':
        assert 'ONCE_BODY' in str(calls[0].messages)
    else:
        assert 'ONCE_BODY' not in str(calls[0].messages)
    if phase == 'B':
        await agent.compact()
    snapshot = agent.dump()
    (root / 'snapshot.json').write_text(json.dumps(snapshot))
    await agent.aclose()
    assert agent.closed
    assert agent.dump() == snapshot
    print(json.dumps({'phase':phase, 'model_calls':len(calls), 'summaries':len(summaries),
                      'generation':snapshot['compaction']['generation'], 'session_id':agent.session_id}))

asyncio.run(main())
"""


def test_public_agent_a_b_c_fresh_processes_with_real_packaged_skill(tmp_path):
    directory = tmp_path / "skills" / "once"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: once\ndescription: task\nmetadata:\n  scope: turn\n---\nONCE_BODY")
    results = []
    for phase in ("A", "B", "C"):
        process = subprocess.run(
            [sys.executable, "-c", _PROCESS, str(tmp_path), phase],
            cwd=Path(__file__).resolve().parents[3],
            text=True,
            capture_output=True,
            timeout=20,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        results.append(json.loads(process.stdout))
    assert [row["model_calls"] for row in results] == [2, 2, 2]
    assert [row["summaries"] for row in results] == [0, 1, 0]
    assert [row["generation"] for row in results] == [0, 1, 1]
    assert len({row["session_id"] for row in results}) == 1
    assert (tmp_path / "effects").read_text().splitlines() == ["A", "B", "C"]


async def test_safe_batch_snapshot_restores_active_turn_only_for_explicit_continue(tmp_path):
    from xdog.agent.events import SnapshotEvent

    directory = tmp_path / "once"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: once\ndescription: test\nmetadata:\n  scope: turn\n---\nACTIVE_TURN_RULE"
    )
    manager = SkillManager(tmp_path, packaged={})
    calls = []

    async def execute(*args, **kwargs):
        return AgentToolResult(content=(TextContent(text="confirmed result"),))

    async def source_stream(*args):
        calls.append(args)
        if len(calls) == 1:
            return EventStream.empty(
                AssistantMessage(content=(ToolCall(id="completed", name="read", arguments={}),), stop_reason="toolUse")
            )
        await asyncio.Event().wait()

    source = Agent(
        source_stream,
        config=AgentConfig(model="offline/test", emit_snapshots=True),
        skills=manager,
        active_skills=["once"],
        tools=[AgentTool(name="read", parameters={"type": "object"}, execute=execute)],
    )
    stream = await source.prompt("original task")
    iterator = stream.__aiter__()
    async with asyncio.timeout(3):
        async for event in iterator:
            if isinstance(event, SnapshotEvent):
                saved = event.snapshot
                saved["messages"].clear()
                saved = event.snapshot  # A consumer cannot mutate the frozen event data.
                assert saved["messages"]
                break
    await source.aclose()
    await iterator.aclose()
    assert saved["run_active"] and not saved["interrupted"]
    assert saved["skills"][0]["lifecycle"] == "active"
    assert saved["messages"][-1]["role"] == "toolResult"

    restored_calls = []

    def restored_stream(model, context, options):
        restored_calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="finished"),)))

    target = Agent(restored_stream, config=AgentConfig(model="offline/test"), skills=manager)
    target.restore(saved)
    assert not restored_calls
    assert target.dump()["skills"][0]["lifecycle"] == "active"
    await (await target.continue_()).collect()
    assert "ACTIVE_TURN_RULE" in str(restored_calls[-1].messages)
    assert target.run_id == saved["run_id"]
    await (await target.prompt("next task")).collect()
    assert "ACTIVE_TURN_RULE" not in str(restored_calls[-1].messages)
    await target.aclose()

    fresh_task = Agent(restored_stream, config=AgentConfig(model="offline/test"), skills=manager)
    fresh_task.restore(saved)
    await (await fresh_task.prompt("different task, not a resume")).collect()
    assert "ACTIVE_TURN_RULE" not in str(restored_calls[-1].messages)
    assert fresh_task.run_id != saved["run_id"]
    await fresh_task.aclose()


async def test_finalizer_failure_can_export_unknown_effect_without_changing_legacy_error_behavior():
    effects = []

    async def execute(*args, **kwargs):
        effects.append("written")
        return AgentToolResult(content=(TextContent(text="external write completed"),))

    async def fail_finalize(*args):
        raise OSError("host finalizer failed")

    agent = Agent(
        lambda *args: EventStream.empty(
            AssistantMessage(content=(ToolCall(id="write-1", name="write", arguments={}),), stop_reason="toolUse")
        ),
        config=AgentConfig(model="offline/test"),
        tools=[AgentTool(name="write", parameters={"type": "object"}, execute=execute)],
        after_tool_call=fail_finalize,
    )
    await asyncio.wait_for((await agent.prompt("task")).collect(), 2)
    assert effects == ["written"]
    assert agent.messages[-1].stop_reason == "error"  # Existing error semantics are retained.
    with pytest.raises(RuntimeError, match="safe snapshot"):
        agent.dump()
    saved = agent.dump(recovery=True)
    assert saved["messages"][-1]["error_message"] == "host finalizer failed"
    assert saved["interrupted_tools"] == [{"tool_call_id": "write-1", "tool_name": "write", "status": "unknown"}]
    await agent.aclose()
    assert agent.dump(recovery=True) == saved
    target = plain_agent()
    with pytest.raises(ValueError, match="reconciliation"):
        target.restore(saved)
    unsafe = copy.deepcopy(saved)
    unsafe["interrupted"] = False
    unsafe["interrupted_tools"] = []
    with pytest.raises(ValueError, match="unfinished tool batch"):
        target.restore(unsafe)
    await target.aclose()
