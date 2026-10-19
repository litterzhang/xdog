"""Real worker report -> Eyes -> leased group inbox -> main Agent -> independent Gate."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.types import GoalStatus, TaskStatus

from packages.claw.tests.eyes_helpers import managed as managed
from packages.claw.tests.test_goal_owned_observers import watching


async def until(predicate):
    async with asyncio.timeout(8):
        while not predicate():
            await asyncio.sleep(.01)


@pytest.mark.parametrize('observer_ack_lost', [False, True])
async def test_worker_report_wakes_only_group_main_then_gate_accepts_and_reports_once(managed, observer_ack_lost):
    runtime = managed.orch._runtimes['g1']
    runtime._enabled_tools = ('goal', 'filesystem', 'eyes')
    runtime._tools = None
    runtime.workspace_dir.mkdir(parents=True, exist_ok=True)
    (runtime.workspace_dir / 'artifact.txt').write_text('correct')
    holder, contexts = {}, []
    release = threading.Event()

    def stream(model, context, options=None):
        contexts.append(context)
        last = context.messages[-1]
        tools = {tool.name for tool in context.tools}
        text = (last.content if isinstance(last.content, str) else ''.join(
            part.text for part in last.content if isinstance(part, TextContent)))
        goal = runtime.goal_manager.owned('g1', holder['goal'])
        if 'task_report' in tools and last.role == 'user':
            message = AssistantMessage(content=(ToolCall(id='worker-report', name='task_report', arguments={
                'revision': 1, 'state': 'submitted', 'summary': 'Checked actual artifact',
                'evidence': ['artifact.txt'], 'blockers': []}),))
        elif 'goal_review' in tools and last.role == 'user':
            message = AssistantMessage(content=(ToolCall(id='inspect', name='filesystem', arguments={
                'action': 'read', 'path': 'artifact.txt'}),))
        elif 'goal_review' in tools and last.role == 'toolResult' and last.tool_name == 'filesystem':
            message = AssistantMessage(content=(ToolCall(id='review', name='goal_review', arguments={
                'verdict': 'passed', 'conditions': [{'condition': 'artifact.txt contains correct',
                    'status': 'passed', 'evidence': ['artifact.txt independently read'],
                    'reason': 'Actual file content matches the original requirement'}],
                'summary': 'Verified artifact'}),))
        elif last.role == 'user' and 'task_report' not in tools and 'goal_review' not in tools:
            if goal.status == GoalStatus.COMPLETED:
                message = AssistantMessage(content=(TextContent(text='Goal verified and complete.'),))
            else:
                assert goal.id in runtime._active_session._goal_touched, (
                    'Owned Goal hint must be bound before model work')
                message = AssistantMessage(content=(ToolCall(id='accept-task', name='goal', arguments={
                    'action': 'update_task', 'goal_id': goal.id, 'task_id': holder['task'],
                    'task_status': 'complete', 'summary': 'Read and accepted current task report'}),))
        elif last.role == 'toolResult' and last.tool_name == 'goal' and 'Pending independent review' not in text:
            message = AssistantMessage(content=(ToolCall(id='request-review', name='goal', arguments={
                'action': 'request_complete', 'goal_id': goal.id, 'summary': 'All actual results checked',
                'evidence_paths': ['artifact.txt']}),))
        else:
            message = AssistantMessage(content=(TextContent(text='Waiting for the independent Goal Gate.'),))
        async def events():
            if 'task_report' in tools and last.role == 'user':
                await asyncio.to_thread(release.wait)  # Cross-loop rendezvous, not an asyncio.Event from the host loop.
            yield DoneEvent(message=message)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(events(), result_future=future)

    runtime.stream_fn = stream
    session = runtime.get_or_create_session(allow_reset=False)
    session._restored = True
    manager = runtime.goal_manager
    goal = manager.create('g1', 'Evidence goal', 'Produce a correct artifact',
                          ['artifact.txt contains correct'], ['Check the artifact'])
    holder.update(goal=goal.id, task=goal.tasks[0].id)
    try:
        assignment = await manager.assign(session, goal.id, goal.tasks[0].id)
        observer = manager.owned('g1', goal.id).observer_id
        await watching(managed, observer)
        assert not await managed.server.backend.inbox.records('g1', runtime.workspace_dir)
        if observer_ack_lost:
            # The observer journal committed, but the Goal's acknowledgement did
            # not. Its trusted owner still binds the first original-ID delivery.
            manager._put(manager.owned('g1', goal.id), observer_id='')
            manager._watchers.discard(goal.id)
        release.set()
        # No Goal tick/polling is called here: the actual file source alone wakes the main Agent.
        await until(lambda: manager.owned('g1', goal.id).phase == 'review_requested')
        assert runtime._active_session is session
        current = manager.owned('g1', goal.id)
        assert current.tasks[0].status == TaskStatus.COMPLETED
        assert current.status == GoalStatus.ACTIVE
        records = await managed.server.backend.inbox.records('g1', runtime.workspace_dir)
        assert len(records) == 1 and records[0].message.source == 'Eyes'
        await until(lambda: not session.children.supervisor.capture_source(assignment['agent_id']).settling)
        await until(lambda: not managed.orch.is_group_running('g1'))
        managed.channel.send_message.assert_not_awaited()
        await manager.tick()
        reviewing = manager.owned('g1', goal.id)
        await until(lambda: not session.children.supervisor.capture_source(reviewing.review_child_id).settling)
        await manager.tick()
        assert manager.owned('g1', goal.id).status == GoalStatus.COMPLETED
        assert managed.orch._runtimes['g2']._active_session is None
        assert managed.orch._runtimes['g3']._active_session is None
        await manager.tick()  # The accepted result reaches the same group main and its normal channel.
        managed.channel.send_message.assert_awaited_once_with('g1', 'Goal verified and complete.')
        before = len(contexts)
        await manager.tick()
        assert len(contexts) == before
    finally:
        release.set()
