"""Record changes chosen by the main Agent. No agent execution or semantic verdicts."""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, replace
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from xdog.agent.async_work import run_owned, wait_owned

from .codec import decode_task, json_text
from .files import TaskFiles, _read, directory
from .host import OperationContext, OwnerRetired, require_user
from .models import TASK_STATES, Goal, Task, identity, text
from .store import GoalStore

P = ParamSpec("P")
T = TypeVar("T")


class GoalService:
    def __init__(self, store: GoalStore, *, valid: Callable[[], bool] = lambda: True,
                 on_change: Callable[[], None] = lambda: None) -> None:
        self.store, self.valid, self.on_change = store, valid, on_change
        self.info: Callable[[], dict[str, Any]] = lambda: {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._invocation: ContextVar[tuple[asyncio.Task[Any], tuple[Callable[[], bool], ...]] | None] = (
            ContextVar('goal_invocation', default=None))

    @contextmanager
    def invocation(self, valid: Callable[[], bool]) -> Iterator[None]:
        """Pin this actual invocation, not unrelated tasks inheriting its context.

        Owned IO threads inherit the pin. New event/refresh tasks do not acquire
        the originating parent's authority merely by inheriting ContextVars.
        Nested pins only narrow authority; they cannot replace an outer fence.
        """
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError('Goal invocation requires its actual owner task')
        previous = self._invocation.get()
        checks = previous[1] if previous is not None and previous[0] is task else ()
        token = self._invocation.set((task, (*checks, valid)))
        try:
            self._check()
            yield
        finally:
            self._invocation.reset(token)

    def _invocation_valid(self) -> bool:
        pin = self._invocation.get()
        if pin is None:
            return True
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None  # asyncio.to_thread copies the admitted operation's context.
        if current is not None and current is not pin[0]:
            return True
        return all(check() for check in pin[1])

    def _captured_check(self) -> Callable[[], None]:
        pin = self._invocation.get()
        if pin is not None and pin[0] is not asyncio.current_task():
            pin = None
        def check() -> None:
            token = self._invocation.set(pin)
            try:
                self._check()
            finally:
                self._invocation.reset(token)
        return check

    async def _io(self, operation: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        check = self._captured_check()
        check()
        @wraps(operation)
        def checked(*args: P.args, **kwargs: P.kwargs) -> T:
            check()  # Fence retirement while the owned worker was awaiting admission.
            return operation(*args, **kwargs)
        result = await run_owned(checked, *args, **kwargs)
        self._check()
        return result

    def _check(self) -> None:
        if self._closed or not self.valid() or not self._invocation_valid():
            raise OwnerRetired('Goal owner retired or closed')

    def files(self, goal: Goal) -> TaskFiles:
        return TaskFiles(self.store.owner.workspace, goal.id)

    async def current(self) -> Goal | None:
        self._check()
        goal = await self.store.current()
        self._check()
        return goal

    async def _selected(self, identifier: str = '') -> Goal:
        goal = await self.current()
        if goal is None or identifier and goal.id != identifier:
            raise ValueError('No matching current Goal; historical/other Goal is not executable')
        return goal

    async def create(self, title: str, outcome: str, criteria: tuple[str, ...] = (), *,
                     context: OperationContext) -> Goal:
        require_user(context)
        async with self._lock:
            self._check()
            if await self.current() is not None:
                raise ValueError('This agent already has a current Goal; complete or clear it first')
            now = time.time()
            goal = Goal('goal-' + uuid.uuid4().hex, title, outcome, criteria, created_at=now, updated_at=now)
            await self._io(self.files(goal).initialize)
            self._check()
            await self.store.put(goal, expected_version=0, check=self._captured_check())
            self.on_change()
            return goal

    async def change(self, action: str, identifier: str = '', *, context: OperationContext = OperationContext(),
                     version: int | None = None, **values: Any) -> Goal:
        async with self._lock:
            goal = await self._selected(identifier)
            if version is not None and (type(version) is not int or version != goal.version):
                raise ValueError('Goal version changed; re-read before updating')
            if (goal.status == 'waiting' and goal.wait_for in {'user', 'host'}
                    and action in {'wait', 'resume', 'complete', 'pause'}):
                require_user(context)  # An intermediate wait(tasks) is not a new delegation.
            if action in {'clear', 'update'}:
                require_user(context)
            if action == 'resume' and not (goal.status == 'waiting' and goal.wait_for == 'tasks'):
                require_user(context)
            if goal.status == 'paused' and action not in {'resume', 'clear', 'update'}:
                require_user(context)  # An automatic reminder cannot turn a user pause into wait/complete.
            changes: dict[str, Any] = {'version': goal.version + 1, 'updated_at': time.time()}
            if action == 'update':
                if not values or set(values) - {'title', 'outcome', 'criteria'}:
                    raise ValueError('Update only title/outcome/criteria')
                changes.update(values)
            elif action in {'pause', 'wait', 'resume', 'complete', 'clear'}:
                status = {'pause': 'paused', 'wait': 'waiting', 'resume': 'active',
                          'complete': 'completed', 'clear': 'cleared'}[action]
                if set(values) - {'reason', 'summary', 'until'}:
                    raise ValueError('Invalid Goal lifecycle fields')
                changes.update(status=status, reason=values.get('reason', ''),
                               summary=values.get('summary', goal.summary),
                               wait_for=values.get('until', 'user') if action == 'wait' else '')
                if action == 'complete':
                    text(values.get('summary', ''))  # Statement by the main Agent, not a script verdict.
            else:
                raise ValueError('Unknown Goal action')
            current = replace(goal, **changes)
            self._check()
            await self.store.put(current, expected_version=goal.version, wake=action == 'resume',
                                 check=self._captured_check())
            self.on_change()
            return current

    async def plan(self, content: str, identifier: str = '') -> Goal:
        async with self._lock:
            goal = await self._selected(identifier)
            if goal.status == 'paused':
                raise ValueError('Resume the user-paused Goal before planning')
            await self._io(self.files(goal).write_plan, content)
            self._check()
            current = replace(goal, version=goal.version + 1, updated_at=time.time())
            await self.store.put(current, expected_version=goal.version, check=self._captured_check())
            self.on_change()
            return current

    async def task(self, values: dict[str, Any], identifier: str = '') -> Task:
        async with self._lock:
            goal = await self._selected(identifier)
            if goal.status == 'paused':
                raise ValueError('Resume the user-paused Goal before editing tasks')
            fields = set(Task.__dataclass_fields__)
            if set(values) - fields - {'expected_version'}:
                raise ValueError('Unsupported task fields')
            data = dict(values)
            expected = data.pop('expected_version', None)
            if type(expected) is not int or expected < 0:
                raise ValueError('Task update requires expected_version (0 for create)')
            data.pop('version', None)
            for name in ('depends_on', 'evidence'):
                if name in data:
                    if not isinstance(data[name], (tuple, list)):
                        raise ValueError('Expected task text array')
                    data[name] = tuple(data[name])
            identifier = identity(data.get('id'))
            files = self.files(goal)
            if expected == 0:
                result = await self._io(files.write, Task(**data), expected_version=0)
            else:
                data.pop('id')
                result = await self._io(files.update, identifier, expected, data)
            self._check()
            self.on_change()
            return result

    async def report(self, values: dict[str, Any], identifier: str = '') -> dict[str, Any]:
        """Record the main Agent's account, never infer progress or release a wait."""
        async with self._lock:
            goal = await self._selected(identifier)
            if goal.status not in {'active', 'waiting'}:
                raise ValueError('Resume the user-paused Goal before reporting')
            fields = {'expected_version', 'progress', 'completed', 'evidence', 'blockers', 'next_steps', 'summary'}
            if not isinstance(values, dict) or set(values) != fields:
                raise ValueError('Report requires exactly the documented report fields')
            expected = values['expected_version']
            if type(expected) is not int or expected < 0:
                raise ValueError('Report requires expected_version (0 for baseline)')
            if not isinstance(values['progress'], str) or values['progress'] not in {
                    'baseline', 'advanced', 'unchanged', 'regressed'}:
                raise ValueError('Invalid report progress label')
            data = dict(values)
            for name in ('completed', 'evidence', 'blockers', 'next_steps'):
                if not isinstance(data[name], list):
                    raise ValueError('Expected report text array')
                data[name] = [text(item) for item in data[name]]
            data['summary'] = text(data['summary'], empty=True)
            result = await self._io(self.files(goal).report, data)
            self._check()
            self.on_change()
            return result

    async def _read_goal(self, identifier: str) -> Goal | None:
        self._check()
        if not isinstance(identifier, str):
            raise ValueError('Invalid Goal identity')
        goal = await self.store.get(identity(identifier)) if identifier else await self.current()
        self._check()
        return goal

    @staticmethod
    def _task_facts(files: TaskFiles) -> dict[str, Any]:
        # No stored task counters: external edits remain authoritative. Scan only
        # task files, never the plan or the report history, on the owned IO worker.
        tasks = files.list()
        counts = dict.fromkeys(sorted(TASK_STATES), 0)
        digest = hashlib.sha256()
        for task in tasks:
            counts[task.status] += 1
            digest.update(json_text(asdict(task), maximum=None).encode())
            digest.update(b'\n')
        preview = [{'id': task.id, 'title': task.title[:160], 'status': task.status,
                    'worker': task.worker[:160], 'version': task.version,
                    'preview_fields': [name for name in ('title', 'worker') if len(getattr(task, name)) > 160]}
                   for task in tasks[:16]]
        return {'task_count': len(tasks), 'task_counts': counts, 'tasks_revision': digest.hexdigest(),
                'tasks_preview': preview, 'tasks_preview_only': len(tasks) > len(preview)}

    @staticmethod
    def _next_fact(goal: Goal | None, info: dict[str, Any]) -> dict[str, Any]:
        """Display guidance derived only from recorded state; never execution authority.

        Observer health is not reminder delivery or Agent execution. Do not infer
        completion/progress from counters, idle execution or a watching observer.
        """
        action, source, reason, requires_user = 'main_agent_work', 'goal', 'Goal is active', False
        if goal is None or goal.status in {'completed', 'cleared'}:
            action, reason, requires_user = 'create', 'No current executable Goal', True
        elif goal.status == 'paused':
            action, reason, requires_user = 'user_resume', goal.reason or 'Goal is paused', True
        elif goal.status == 'waiting' and goal.wait_for in {'user', 'host'}:
            action = 'user_input' if goal.wait_for == 'user' else 'host_recovery'
            reason, requires_user = goal.reason or f'Goal waits for {goal.wait_for}', True
        else:
            execution, delivery = info.get('execution', {}), info.get('delivery', {})
            if delivery.get('status') == 'unknown':
                action, source, reason, requires_user = (
                    'inspect_delivery', 'delivery', 'Reminder outcome is unknown; do not replay blindly', True)
            elif execution.get('state') in {'unavailable', 'closing'}:
                action, source, reason = (
                    'await_owner_available', 'execution', 'Execution owner is unavailable or closing')
            elif execution.get('state') not in {None, 'idle'}:
                action, source, reason = 'await_execution', 'execution', 'Host execution is not idle'
            elif execution.get('user_pending') is True:
                action, source, reason = 'await_user_turn', 'execution', 'Host has pending user input'
            elif execution.get('children_active') is True:
                action, source, reason = 'await_children', 'execution', 'Host has active children'
            elif delivery.get('status') == 'unavailable':
                action, source, reason = (
                    'restore_delivery', 'delivery', 'Reminder delivery is unavailable')
            elif delivery.get('status') == 'deferred':
                action, source, reason = 'await_owner_available', 'delivery', 'Reminder delivery is deferred'
            elif goal.status == 'waiting':
                action, reason = 'inspect_tasks', goal.reason or 'Goal waits for task facts'
        return {'action': action, 'source': source, 'reason': reason, 'requires_user': requires_user}

    async def summary(self, identifier: str = '') -> dict[str, Any]:
        """Native /goal status and model default: facts, counts, bounded previews.

        No plan body, full task bodies or report history. This is also exposed by
        status(detail=False); status() keeps the original detailed contract.
        latest_report is the Agent's recorded assessment, not a service verdict.
        Host info (automation/delivery/execution/legacy_storage) is preserved;
        next is display guidance only and grants no authority or scheduling.
        """
        return await self.status(identifier, detail=False)

    async def status(self, identifier: str = '', *, detail: bool = True) -> dict[str, Any]:
        if type(detail) is not bool:
            raise ValueError('Goal status detail must be a boolean')
        async with self._lock:
            goal = await self._read_goal(identifier)
            if goal is None:
                info = self.info()
                return {'format': 'xdog.goal_status.v2', 'current': None, **info,
                        **({'next': self._next_fact(None, info)} if not detail else {})}
            files = self.files(goal)
            value: dict[str, Any] = {'format': 'xdog.goal_status.v2', 'current': asdict(goal),
                                     'workspace': str(files.root), 'plan_path': str(files.root / 'plan.md'),
                                     'task_dir': str(files.tasks_dir)}
            if detail:
                tasks = await self._io(files.list)
                value.update(tasks=await self._io(lambda: [asdict(task) for task in tasks]),
                             plan=await self._io(files.plan))
            else:
                value.update(await self._io(self._task_facts, files))
                value.update(report_path=str(files.progress_path),
                             latest_report=await self._io(files.latest_report))
            info = self.info()  # Local host snapshots only; no endpoint/network probing.
            if not detail:
                value['next'] = self._next_fact(goal, info)
            return {**value, **info}

    async def read_plan(self, identifier: str = '') -> dict[str, Any]:
        """Read only the selected plan; no task scan or report IO."""
        async with self._lock:
            goal = await self._read_goal(identifier)
            if goal is None:
                raise ValueError('No current Goal')
            files = self.files(goal)
            return {'format': 'xdog.goal_plan.v1', 'goal_id': goal.id,
                    'plan_path': str(files.root / 'plan.md'), 'plan': await self._io(files.plan)}

    async def tasks(self, identifier: str = '', *, task_id: str = '', status: str = '',
                    worker: str = '') -> dict[str, Any]:
        """Read a single file or a filtered snapshot; tool frames page its bytes.

        Filters are conjunctive. Single-task reads do not scan unrelated files;
        missing tasks are errors, whereas a filter with no matches is empty.
        """
        if not isinstance(task_id, str) or not isinstance(status, str) or status and status not in TASK_STATES:
            raise ValueError('Invalid task identity/status filter')
        text(worker, empty=True, maximum=1024)
        if task_id:
            identity(task_id)
        async with self._lock:
            goal = await self._read_goal(identifier)
            if goal is None:
                raise ValueError('No current Goal')
            files = self.files(goal)

            def read() -> list[dict[str, Any]]:
                selected: tuple[Task, ...]
                if task_id:
                    with directory(files.tasks_dir) as fd:
                        try:
                            task = decode_task(_read(fd, task_id + '.json'))
                        except FileNotFoundError:
                            raise ValueError('Unknown task') from None
                    if task.id != task_id:
                        raise ValueError('Task filename and identity disagree')
                    selected = (task,)
                else:
                    selected = files.list()
                return [asdict(task) for task in selected
                        if (not status or task.status == status) and (not worker or task.worker == worker)]

            return {'format': 'xdog.goal_tasks.v1', 'goal_id': goal.id, 'task_dir': str(files.tasks_dir),
                    'tasks': await self._io(read)}

    async def list(self) -> tuple[Goal, ...]:
        async with self._lock:
            self._check()
            result = await self.store.list()
            self._check()
            return result

    async def _close(self) -> None:
        async with self._lock:
            await self.store.close()

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close())
        await wait_owned(self._close_task)
