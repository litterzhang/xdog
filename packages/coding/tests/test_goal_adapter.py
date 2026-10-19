"""Real Coding Agent/children/permission and Inbox, scripted model transport only."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.goal_adapter import CodingGoalHost
from xdog.goal.host import OperationContext
from xdog.goal.models import Notice, WorkerRef, report_digest

from packages.coding.tests.test_child_agents import create


def scripted(contexts, *, try_write=False):
    def stream(model, context, options):
        contexts.append(context)
        names = {tool.name for tool in context.tools}
        worker = "task_report" in names
        reviewer = "goal_context" in names and not worker
        round_number = sum(message.role == "assistant" for message in context.messages)
        if (worker or reviewer) and round_number == 0:
            parts = (ToolCall(id="contract", name="goal_context", arguments={}),)
        elif (worker or reviewer) and round_number == 1:
            parts = (ToolCall(id="read", name="filesystem", arguments={"action": "read", "path": "result.txt"}),)
        elif worker and round_number == (3 if try_write else 2):
            parts = (
                ToolCall(
                    id="report",
                    name="task_report",
                    arguments={
                        "revision": 1,
                        "state": "submitted",
                        "summary": "Inspected output",
                        "evidence": ["result.txt"],
                        "blockers": [],
                    },
                ),
            )
        elif (
            ((worker or reviewer) and try_write and round_number == 2) or not (worker or reviewer) and round_number == 0
        ):
            parts = (
                ToolCall(
                    id="write",
                    name="filesystem",
                    arguments={"action": "write", "path": "forbidden.txt", "content": "must not happen"},
                ),
            )
        elif reviewer:
            parts = (
                TextContent(
                    text=json.dumps(
                        {
                            "verdict": "passed",
                            "summary": "Independent file read",
                            "conditions": [
                                {
                                    "condition": "Result exists",
                                    "status": "passed",
                                    "evidence": ["result.txt"],
                                    "reason": "Read actual output",
                                }
                            ],
                        }
                    )
                ),
            )
        else:
            parts = (TextContent(text="done"),)
        message = AssistantMessage(content=parts, stop_reason="toolUse" if isinstance(parts[0], ToolCall) else "stop")

        async def events():
            yield DoneEvent(message=message)

        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)

    return stream


async def setup(tmp_path, *, mode="ask", try_write=False):
    session, _ = create(tmp_path, mode)
    tmp_path.joinpath("result.txt").write_text("real output")
    contexts = []
    session.agent.set_stream_fn(scripted(contexts, try_write=try_write))
    host = await CodingGoalHost.open(session)
    return session, host, contexts


async def close(session, host):
    await session.children.supervisor.aclose()
    await host.aclose()
    await session.extensions.aclose()
    session.dispose()


async def test_actual_coding_worker_acceptance_independent_readonly_review_and_durable_notices(tmp_path):
    session, host, contexts = await setup(tmp_path, try_write=True)
    try:
        s = host.service
        goal = await s.create("Output", "Inspect file", ("Result exists",), context=OperationContext(True))
        goal = await s.add_task(goal.id, "Read output")
        goal = await s.assign(goal.id, goal.tasks[0].id, "Inspect output and report")
        reference = goal.tasks[0].attempts[-1].worker
        await session.children.supervisor.control("wait", reference.id)
        goal = await s.status(goal.id)
        task = goal.tasks[0]
        attempt = task.attempts[-1]
        assert task.state == "submitted" and goal.status != "completed"
        saved_worker = await session.children.supervisor.control("inspect", reference.id)
        assert any(
            message.get("tool_name") == "goal_context" and "Result exists" in str(message["content"])
            for message in saved_worker["messages"]
        )
        goal = await s.accept_task(goal.id, task.id, attempt.id, 1, report_digest(attempt.reports[-1]), "Read")
        goal = await s.request_complete(goal.id, paths=("result.txt",))
        assert goal.review.worker != reference
        await session.children.supervisor.control("wait", goal.review.worker.id)
        goal = await s.reconcile(goal.id)
        assert goal.status == "completed"
        assert not (tmp_path / "forbidden.txt").exists()
        await host.flush()
        records = await asyncio.to_thread(host.inbox.records, session.session_id, tmp_path)
        assert records and all(row.status == "pending" for row in records)
        assert all(
            row.message.source == "goal" and row.branch_id == session.session_data.active_branch for row in records
        )
        assert len({row.message.message_id for row in records}) == len(records)
        assert any("goal_context" in {tool.name for tool in ctx.tools} for ctx in contexts)
        assert not session.messages  # No second parent loop/implicit chat turn.
    finally:
        await close(session, host)


async def test_ordinary_child_forged_goal_label_cannot_gain_bound_role_or_write(tmp_path):
    session, host, contexts = await setup(tmp_path)
    try:
        identifier = await session.children.supervisor.start("Inspect", config={"label": "goal-worker"})
        result = await session.children.supervisor.control("wait", identifier)
        assert result["status"] == "completed"
        assert not {"task_report", "goal_context", "goal"} & {tool.name for tool in contexts[0].tools}
        assert not (tmp_path / "forbidden.txt").exists()
        with pytest.raises(ValueError, match="execution"):
            await host.cancel_child(WorkerRef(identifier, "forged-execution"))
    finally:
        await close(session, host)


async def test_goal_notices_keep_exact_branch_content_and_original_id_without_approval(tmp_path):
    session, host, _ = await setup(tmp_path)
    notice = Notice("notice-original", "goal-original", "Inspect original task")
    try:
        assert (await host.enqueue(notice)).status == "accepted"
        assert (await host.enqueue(notice)).status == "duplicate"
        assert (await host.receipt(notice)).owner_key == host.owner.key
        with pytest.raises(ValueError, match="conflict"):
            await host.enqueue(replace(notice, instruction="Different content"))
        with pytest.raises(ValueError, match="conflict"):
            await host.receipt(replace(notice, instruction="Different content"))
        previous = host.owner
        session.session_data.active_branch = "successor"
        session.session_manager.save_session(session.session_data)
        with pytest.raises(ValueError, match="owner|binding"):
            await host.enqueue(Notice("late", "goal-original", "Must not move"))
        with pytest.raises(ValueError, match="owner|binding"):
            await host.service.list()
        assert host.owner != previous  # stale service's owner fence can observe the change
    finally:
        await close(session, host)


async def test_role_allowance_is_bound_to_actual_tool_object_not_a_name(tmp_path):
    session, host, _ = await setup(tmp_path)
    try:
        s = host.service
        goal = await s.create("Output", "Inspect file", ("Result exists",), context=OperationContext(True))
        goal = await s.add_task(goal.id, "Read output")
        goal = await s.assign(goal.id, goal.tasks[0].id, "Inspect")
        ref = goal.tasks[0].attempts[-1].worker
        await session.children.supervisor.control("wait", ref.id)
        tools = host.bound_tools[ref.id]
        assert host.role_call(ref.id, "task_report", tools)
        report = next(tool for tool in tools if tool.name == "task_report")
        assert not host.role_call(ref.id, "task_report", (replace(report),))
        assert not host.role_call(ref.id, "filesystem", tools)
    finally:
        await close(session, host)


@pytest.mark.parametrize("mode", ["ask", "deny", "allow-all"])
async def test_inherited_goal_worker_preserves_actual_coding_mutation_policy(tmp_path, mode):
    session, host, contexts = await setup(tmp_path, mode=mode, try_write=True)
    try:
        s = host.service
        goal = await s.create("Output", "Inspect file", ("Result exists",), context=OperationContext(True))
        goal = await s.add_task(goal.id, "Read output")
        goal = await s.assign(goal.id, goal.tasks[0].id, "Inspect", access="inherit")
        reference = goal.tasks[0].attempts[-1].worker
        await session.children.supervisor.control("wait", reference.id)
        assert (tmp_path / "forbidden.txt").exists() == (mode == "allow-all")
        assert (await s.status(goal.id)).tasks[0].state == "submitted"
        assert all("goal" not in {tool.name for tool in ctx.tools} for ctx in contexts)
    finally:
        await close(session, host)


async def test_simultaneous_ordinary_start_does_not_borrow_goal_admission(tmp_path):
    session, host, _ = await setup(tmp_path)
    try:
        s = host.service
        goal = await s.create("Output", "Inspect file", ("Result exists",), context=OperationContext(True))
        goal = await s.add_task(goal.id, "Read output")
        assigned, ordinary = await asyncio.gather(
            s.assign(goal.id, goal.tasks[0].id, "Inspect"),
            session.children.supervisor.start("Ordinary", config={"label": "goal-worker"}),
        )
        await session.children.supervisor.control("wait", ordinary)
        await session.children.supervisor.control("wait", assigned.tasks[0].attempts[-1].worker.id)
        assert host.role(ordinary) is None and ordinary not in host.bound_tools
        assert (await s.status(goal.id)).tasks[0].state == "submitted"
    finally:
        await close(session, host)


async def test_parent_tool_cannot_fabricate_host_user_authority(tmp_path):
    session, host, _ = await setup(tmp_path)
    try:
        result = await host.tool.execute(
            "create",
            {
                "action": "create",
                "title": "Not delegated",
                "outcome": "Spoof",
                "criteria": ["test"],
            },
        )
        assert result.is_error and await host.service.list() == ()
        spoof = await host.tool.execute(
            "create",
            {
                "action": "create",
                "title": "Not delegated",
                "outcome": "Spoof",
                "criteria": ["test"],
                "user_authorized": True,
            },
        )
        assert spoof.is_error and await host.service.list() == ()
    finally:
        await close(session, host)


async def test_reopen_uses_persisted_terminal_child_execution_without_replaying_worker(tmp_path):
    session, host, contexts = await setup(tmp_path)
    try:
        s = host.service
        goal = await s.create("Output", "Inspect file", ("Result exists",), context=OperationContext(True))
        goal = await s.add_task(goal.id, "Read output")
        goal = await s.assign(goal.id, goal.tasks[0].id, "Inspect")
        reference = goal.tasks[0].attempts[-1].worker
        await session.children.supervisor.control("wait", reference.id)
        await host.flush()
        await host.aclose()
        await session.children.supervisor.aclose()
        session._load_children()  # Real persisted ChildStore catalog, not a scripted status response.
        host = await CodingGoalHost.open(session)
        size = len(contexts)
        goal = await host.service.status(goal.id)
        task = goal.tasks[0]
        attempt = task.attempts[-1]
        goal = await host.service.accept_task(
            goal.id, task.id, attempt.id, 1, report_digest(attempt.reports[-1]), "Inspect saved report"
        )
        assert goal.tasks[0].state == "accepted"
        assert goal.tasks[0].attempts[-1].worker == reference
        assert len(contexts) == size and not session.children.supervisor.active
    finally:
        await close(session, host)


async def test_other_session_cannot_read_or_consume_goal_owner_state(tmp_path):
    session, host, _ = await setup(tmp_path)
    other = other_host = None
    try:
        goal = await host.service.create("Private", "Only original session", ("test",), context=OperationContext(True))
        other, other_host, _ = await setup(tmp_path)
        assert other.session_id != session.session_id and other_host.owner != host.owner
        assert await other_host.service.list() == ()
        with pytest.raises(ValueError, match="Unknown Goal"):
            await other_host.service.status(goal.id)
        assert (await host.service.status(goal.id)).title == "Private"
    finally:
        if other_host is not None:
            await close(other, other_host)
        await close(session, host)
