"""The group main Agent manages goals; only an independent Gate completes them."""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from xdog.agent.core import AgentTool
from xdog.agent.tool_def import Param, ToolDef, action
from xdog.claw.core.planning.goal_manager import GoalManager
from xdog.claw.core.types import GoalStatus


def _host(ctx: dict[str, Any]) -> tuple[GoalManager, str, Any]:
    manager, session = ctx.get('_goal_manager'), ctx.get('_goal_agent')
    group = ctx.get('group_id')
    if (not isinstance(manager, GoalManager) or session is None or not isinstance(group, str)
            or session._runtime._active_session is not session or session.meta.group_id != group):
        raise ValueError('Goal management belongs to the current group main Agent only')
    if manager.tracker.diagnostic:
        raise ValueError(manager.tracker.diagnostic)
    return manager, group, session


def _render(goal: Any) -> str:
    return json.dumps(asdict(goal), ensure_ascii=False)


def _touch(session: Any, goal_id: str) -> None:
    session._goal_touched.add(goal_id)


def _user_control(session: Any, operation: str) -> None:
    if not getattr(session, '_goal_user_authorized', False):
        raise ValueError(operation + ' requires an explicit user turn, not events/background work')


class GoalTool(ToolDef):
    name = 'goal'
    description = ('Manage this group main Agent\'s persistent goals and tasks. Plan and work after create; '
                   'only an independent Agent Gate can complete a goal. Load claw-goals for the workflow.')
    required_ctx = ('group_id',)

    @action('create', description='Create an outcome contract, then plan and work; do not wait for a planner',
            title=Param('string', required=True), goal_description=Param('string', required=True),
            verification_conditions=Param('array', required=True, items={'type': 'string'}),
            tasks=Param('array', items={'type': 'string'}))
    async def create(self, ctx: dict[str, Any], title: str, goal_description: str,
                     verification_conditions: Sequence[str], tasks: Sequence[str] = ()) -> str:
        manager, group, session = _host(ctx)
        goal = manager.create(group, title, goal_description, list(verification_conditions), list(tasks))
        _touch(session, goal.id)
        return _render(goal) + '\nYou own this goal. Continue planning/work; assign tasks or work yourself.'

    @action('update', description='Apply an explicit user change of outcome/acceptance; invalidates old review',
            goal_id=Param('string', required=True), goal_description=Param('string', required=True),
            verification_conditions=Param('array', required=True, items={'type': 'string'}),
            reason=Param('string', required=True))
    async def update(self, ctx: dict[str, Any], goal_id: str, goal_description: str,
                     verification_conditions: Sequence[str], reason: str) -> str:
        manager, group, session = _host(ctx)
        _user_control(session, 'Goal criteria changes')
        goal = manager.revise(group, goal_id, goal_description, list(verification_conditions), reason)
        _touch(session, goal_id)
        return _render(goal)

    @action('list', description='Inspect only this group goals',
            status=Param('string', default='active',
                enum=['active', 'paused', 'blocked', 'completed', 'abandoned', 'all']))
    async def list(self, ctx: dict[str, Any], status: str = 'active') -> str:
        manager, group, _ = _host(ctx)
        return json.dumps([asdict(goal) for goal in manager.tracker.list_goals(status)
                           if goal.group_id == group], ensure_ascii=False)

    @action('status', description='Inspect a goal and its workspace', goal_id=Param('string', required=True))
    async def status(self, ctx: dict[str, Any], goal_id: str) -> str:
        manager, group, _ = _host(ctx)
        goal = await manager.reconcile(manager.owned(group, goal_id))
        return _render(goal) + '\nWorkspace: ' + str(manager.workspace.goal_dir(goal.id))

    @action('add_task', description='Add a task; the main Agent decides execution and dependencies',
            goal_id=Param('string', required=True), task_description=Param('string', required=True),
            depends_on=Param('array', items={'type': 'string'}))
    async def add_task(self, ctx: dict[str, Any], goal_id: str, task_description: str,
                       depends_on: Sequence[str] = ()) -> str:
        manager, group, session = _host(ctx)
        goal = manager.add_task(group, goal_id, task_description, tuple(depends_on))
        _touch(session, goal.id)
        return _render(goal)

    @action('assign_task', description='Start a task worker owned by this main Agent and give it its report path',
            goal_id=Param('string', required=True), task_id=Param('string', required=True),
            access=Param('string', enum=['read-only', 'inherit'], default='inherit'),
            executor=Param('string', enum=['main', 'child'], default='child'))
    async def assign_task(self, ctx: dict[str, Any], goal_id: str, task_id: str, access: str = 'inherit',
                          executor: str = 'child') -> str:
        manager, _, session = _host(ctx)
        result = await manager.assign(session, goal_id, task_id, access, executor=executor)
        _touch(session, goal_id)
        return json.dumps(result, ensure_ascii=False)

    @action('report_task', description='Atomically update a report for a task assigned to the main Agent itself',
            goal_id=Param('string', required=True), task_id=Param('string', required=True),
            report=Param('object', required=True,
                description='revision, state, summary, evidence, blockers; identities are host-bound'))
    async def report_task(self, ctx: dict[str, Any], goal_id: str, task_id: str, report: dict[str, Any]) -> str:
        manager, group, session = _host(ctx)
        goal = manager.owned(group, goal_id)
        task = manager.task(goal, task_id)
        await manager.write_report(group, goal_id, task_id, task.attempt_id, 'main', report)
        _touch(session, goal_id)
        return 'Report saved atomically; submitted is not task acceptance or Goal completion.'

    @action('update_task', description='Accept a submitted report or record main-Agent work; never completes Goal',
            goal_id=Param('string', required=True), task_id=Param('string', required=True),
            task_status=Param('string', enum=['complete', 'skip'], required=True),
            summary=Param('string', required=True))
    async def update_task(self, ctx: dict[str, Any], goal_id: str, task_id: str, task_status: str, summary: str) -> str:
        manager, group, session = _host(ctx)
        if task_status not in {'complete', 'skip'}:
            raise ValueError('Task status must be complete or skip')
        goal = await manager.reconcile(manager.owned(group, goal_id))
        goal = manager.accept_task(group, goal.id, task_id, summary, skip=task_status == 'skip')
        _touch(session, goal_id)
        return _render(goal)

    @action('request_complete', description='Request independent Agent review; this is not a completion verdict',
            goal_id=Param('string', required=True), summary=Param('string'),
            evidence_paths=Param('array', items={'type': 'string'},
                description='Relative evidence paths; default is workspace excluding runtime/cache trees'))
    async def request_complete(self, ctx: dict[str, Any], goal_id: str, summary: str = '',
                               evidence_paths: Sequence[str] = ()) -> str:
        manager, group, session = _host(ctx)
        await manager.reconcile(manager.owned(group, goal_id))
        goal = manager.request_review(group, goal_id, summary, tuple(evidence_paths))
        _touch(session, goal_id)
        return _render(goal) + '\nPending independent review, not completed.'

    @action('wait', description='Yield until task reports/events/user action; do not spin',
            goal_id=Param('string', required=True), reason=Param('string', required=True))
    async def wait(self, ctx: dict[str, Any], goal_id: str, reason: str) -> str:
        manager, group, session = _host(ctx)
        _touch(session, goal_id)
        return _render(manager.wait(group, goal_id, reason))

    @action('pause', description='Pause automatic goal work; manage active children explicitly',
            goal_id=Param('string', required=True), reason=Param('string'))
    async def pause(self, ctx: dict[str, Any], goal_id: str, reason: str = '') -> str:
        manager, group, session = _host(ctx)
        _user_control(session, 'Goal pause')
        return _render(manager.status(group, goal_id, GoalStatus.PAUSED, reason))

    @action('resume', description='Resume this group goal from saved evidence, never replay interrupted operations',
            goal_id=Param('string', required=True))
    async def resume(self, ctx: dict[str, Any], goal_id: str) -> str:
        manager, group, session = _host(ctx)
        current = manager.owned(group, goal_id)
        if current.status == GoalStatus.PAUSED:
            _user_control(session, 'A paused goal resume')
        _touch(session, goal_id)
        return _render(manager.status(group, goal_id, GoalStatus.ACTIVE))

    @action('abandon_goal', description='Abandon without deleting workspace evidence',
            goal_id=Param('string', required=True), summary=Param('string'))
    async def abandon_goal(self, ctx: dict[str, Any], goal_id: str, summary: str = '') -> str:
        manager, group, session = _host(ctx)
        _user_control(session, 'Goal abandon')
        await manager.cancel_workers(session, manager.owned(group, goal_id))
        goal = manager.status(group, goal_id, GoalStatus.ABANDONED, summary)
        if goal.observer_id and manager.runtime is not None and manager.runtime.observers is not None:
            await manager.runtime.observers.cancel(group, goal.observer_id)
        return _render(goal)


def create_goal_tool() -> AgentTool:
    return GoalTool().build()
