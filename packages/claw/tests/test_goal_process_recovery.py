"""Independent interpreters restore a completed reviewer receipt, never replay models."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = r'''
import asyncio, json, sys
from pathlib import Path
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.types import Group, GoalStatus
root, phase = Path(sys.argv[1]), sys.argv[2]
workspace = root / 'workspace'
workspace.mkdir(exist_ok=True)
calls_file = root / 'model-calls.json'
def calls():
    return json.loads(calls_file.read_text()) if calls_file.exists() else []
def stream(model, context, options=None):
    if phase != 'A':
        raise AssertionError('Restoring/finalizing the stored receipt must not make a model request')
    history = calls()
    history.append(context.messages[-1].role)
    calls_file.write_text(json.dumps(history))
    last = context.messages[-1]
    if last.role == 'user':
        message = AssistantMessage(content=(ToolCall(id='inspect', name='filesystem', arguments={
            'action': 'read', 'path': 'artifact.txt'}),))
    elif last.role == 'toolResult' and last.tool_name == 'filesystem':
        message = AssistantMessage(content=(ToolCall(id='finding', name='goal_review', arguments={
            'verdict': 'passed', 'conditions': [{'condition': 'artifact.txt contains correct',
                'status': 'passed', 'evidence': ['artifact.txt read independently'],
                'reason': 'Actual content matches'}], 'summary': 'Verified actual artifact'}),))
    else:
        message = AssistantMessage(content=(TextContent(text='Review submitted'),))
    async def events():
        yield DoneEvent(message=message)
    future = asyncio.get_running_loop().create_future()
    future.set_result(message)
    return EventStream.from_async_generator(events(), result_future=future)
async def main():
    runtime = GroupRuntime(group=Group(id='main', enabled_tools=('goal','filesystem')),
        data_dir=root / 'data', workspace_dir=workspace, model='test/model', stream_fn=stream)
    manager = runtime.goal_manager
    session = None
    try:
        if phase == 'A':
            (workspace / 'artifact.txt').write_text('correct')
            session = runtime.get_or_create_session(allow_reset=False)
            session._restored = True
            goal = manager.create('main','Persistent contract','Produce an actual artifact',
                                  ['artifact.txt contains correct'],[])
            manager.request_review('main',goal.id,evidence_paths=('artifact.txt',))
            await manager.tick()
            goal = manager.owned('main',goal.id)
            async with asyncio.timeout(6):
                while session.children.supervisor.capture_source(goal.review_child_id).settling:
                    await asyncio.sleep(.005)
            manager.record_children_status(session.children.supervisor.snapshot()['children'])
            goal = manager.owned('main',goal.id)
            assert goal.phase == 'reviewing' and goal.review_output and goal.review_execution == 'completed'
            assert len(calls()) == 3
        else:
            goal, = manager.tracker.list_goals()
            assert goal.group_id == 'main'
            if phase == 'B':
                assert goal.status == GoalStatus.ACTIVE and goal.review_execution == 'completed'
                await manager.tick()
                session = runtime._active_session
                goal = manager.owned('main',goal.id)
                assert goal.status == GoalStatus.COMPLETED
                assert json.loads(goal.last_verification_run.output)['verdict'] == 'passed'
            else:
                assert goal.status == GoalStatus.COMPLETED and not manager.has_active_goals()
                assert runtime._active_session is None  # Merely restore data: no Agent/model/worker starts.
            assert len(calls()) == 3
        print(json.dumps({'phase':phase,'goal_id':goal.id,'status':goal.status,
                          'model_calls':len(calls()),'review_child_id':goal.review_child_id}))
    finally:
        if session is not None:
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
        await runtime.mcp.aclose()
asyncio.run(main())
'''


def test_three_process_goal_receipt_restore_and_completion_without_model_replay(tmp_path):
    results = []
    for phase in ('A', 'B', 'C'):
        result = subprocess.run([sys.executable, '-c', SCRIPT, str(tmp_path), phase],
            cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        results.append(json.loads(result.stdout))
    assert len({row['goal_id'] for row in results}) == len({row['review_child_id'] for row in results}) == 1
    assert [row['status'] for row in results] == ['active', 'completed', 'completed']
    assert [row['model_calls'] for row in results] == [3, 3, 3]
