"""Record changes chosen by the main Agent. No agent execution or semantic verdicts."""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, replace
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from xdog.agent.async_work import run_owned, wait_owned

from .codec import decode_task, json_text, read_json
from .files import TaskFiles, _read, directory
from .host import OperationContext, OwnerRetired, require_user
from .models import TASK_STATES, Goal, Task, identity, text
from .store import GoalStore, remindable

P = ParamSpec("P")
T = TypeVar("T")


class GoalService:
    def __init__(self, store: GoalStore, *, valid: Callable[[], bool] = lambda: True,
                 on_change: Callable[[], None] = lambda: None) -> None:
        self.store, self.valid, self.on_change = store, valid, on_change
        self.info: Callable[[], dict[str, Any]] = lambda: {}
        self.message_delivery: Callable[[str], Awaitable[dict[str, Any]]] | None = None
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
            current = await self.current()
            if current is not None and current.status != 'completed':
                raise ValueError('This agent already has a current Goal; complete or clear it first')
            now = time.time()
            goal = Goal('goal-' + uuid.uuid4().hex, title, outcome, criteria, created_at=now, updated_at=now)
            await self._io(self.files(goal).initialize)
            self._check()
            await self.store.put(goal, expected_version=0, check=self._captured_check(),
                                 action='create', context=context)
            self.on_change()
            return goal

    async def change(self, action: str, identifier: str = '', *, context: OperationContext = OperationContext(),
                     version: int | None = None, **values: Any) -> Goal:
        async with self._lock:
            goal = await self._selected(identifier)
            if action == 'wait' and values.get('until', 'user') not in {'user', 'tasks'}:
                raise ValueError('Goal wait supports only user or tasks; host faults do not change Goal state')
            if goal.status in {'completed', 'cleared'} and action != 'clear':
                raise ValueError('Completed/historical Goal is read-only; create a new Goal')
            if action == 'activate':
                if type(version) is not int or version < 1:
                    raise ValueError('Activate requires the observed Goal version')
                if (set(values) != {'reason'} or not isinstance(values['reason'], str)
                        or not values['reason'].strip()):
                    raise ValueError('Activate requires the main Agent assessment of the satisfied wait')
                if goal.status == 'active':
                    return goal  # Idempotent, including ACK-loss retry: no revision or wake cause.
                if goal.status != 'waiting':
                    raise ValueError('Activate only releases waiting; a paused Goal requires actual user resume')
            if version is not None and (type(version) is not int or version != goal.version):
                raise ValueError('Goal version changed; re-read before updating')
            same_question = (action == 'wait' and goal.status == 'waiting' and goal.wait_for == 'user'
                             and bool(goal.question) and values.get('until', 'user') == 'user'
                             and not set(values) - {'until', 'reason', 'question', 'impact', 'resume_when'}
                             and all(values.get(name) == getattr(goal, name)
                                     for name in ('question', 'impact', 'resume_when')))
            if same_question:
                text(values.get('reason', goal.reason), empty=True)
                if values.get('reason', goal.reason) == goal.reason:
                    # Exact ACK-loss replay is a read of the original wait, not
                    # authority to alter it. No revision, new reference or wake.
                    return goal
            if action == 'resume':
                if goal.status != 'paused':
                    raise ValueError('Resume only restores paused Goals; use activate for a satisfied wait')
                require_user(context)
            if (goal.status == 'waiting' and goal.wait_for in {'user', 'host'}
                    and action in {'wait', 'activate', 'complete', 'pause'}):
                require_user(context)  # An intermediate wait(tasks) is not a new delegation.
            if action in {'clear', 'update'}:
                require_user(context)
            if goal.status == 'paused' and action not in {'resume', 'clear', 'update'}:
                require_user(context)  # An automatic reminder cannot turn a user pause into wait/complete.
            changes: dict[str, Any] = {'version': goal.version + 1, 'updated_at': time.time()}
            if action == 'update':
                if not values or set(values) - {'title', 'outcome', 'criteria'}:
                    raise ValueError('Update only title/outcome/criteria')
                changes.update(values)
            elif action in {'pause', 'wait', 'activate', 'resume', 'complete', 'clear'}:
                status = {'pause': 'paused', 'wait': 'waiting', 'activate': 'active', 'resume': 'active',
                          'complete': 'completed', 'clear': 'cleared'}[action]
                allowed = {'pause': {'reason'}, 'wait': {'reason', 'until', 'question', 'impact', 'resume_when'},
                           'activate': {'reason'}, 'resume': set(), 'complete': {'summary'}, 'clear': {'reason'}}
                if set(values) - allowed[action]:
                    raise ValueError('Invalid Goal lifecycle fields')
                question_fields = ('question', 'impact', 'resume_when')
                user_wait = action == 'wait' and values.get('until', 'user') == 'user'
                if user_wait:
                    for name in question_fields:
                        if name not in values:
                            raise ValueError(f'wait(user) missing required field: {name}')
                        text(values[name])
                elif set(values) & set(question_fields):
                    raise ValueError('question/impact/resume_when are only supported for wait(user)')
                changes.update({name: values[name] if user_wait else '' for name in question_fields})
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
                                 check=self._captured_check(),
                                 action=action if action != 'update' and not same_question else '', context=context)
            self.on_change()
            return current

    async def plan(self, content: str, identifier: str = '') -> Goal:
        async with self._lock:
            goal = await self._selected(identifier)
            if goal.status in {'completed', 'cleared'}:
                raise ValueError('Completed/historical Goal is read-only; create a new Goal')
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
            if goal.status in {'completed', 'cleared'}:
                raise ValueError('Completed/historical Goal is read-only; create a new Goal')
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

    def _status_info(self, goal: Goal | None) -> dict[str, Any]:
        """Project current eligibility without rewriting transport history or receipts."""
        info = dict(self.info())
        delivery = info.get('delivery', {})
        if delivery.get('goal_id') and (goal is None or delivery['goal_id'] != goal.id):
            info['last_delivery'] = dict(delivery)
            info['delivery'] = {'status': 'idle', 'reason': 'Delivery belongs to a previous Goal'}
        elif delivery.get('status') == 'deferred' and (goal is None or not remindable(goal)):
            reason = ('No current Goal' if goal is None else f'Goal waits for {goal.wait_for}'
                      if goal.status == 'waiting' else f'Goal is {goal.status}')
            info['last_delivery'] = dict(delivery)
            info['delivery'] = {'status': 'idle', 'reason': reason + '; automatic reminders disabled'}
        return info

    @staticmethod
    def _next_fact(goal: Goal | None, info: dict[str, Any], *, for_agent: bool = False) -> dict[str, Any]:
        """Display guidance derived only from recorded state; never execution authority.

        Observer health is not reminder delivery or Agent execution. Do not infer
        completion/progress from counters, idle execution or a watching observer.
        """
        action, source, reason, requires_user = 'main_agent_work', 'goal', 'Goal is active', False
        if goal is None:
            action, reason, requires_user = 'create', 'No current Goal', True
        elif goal.status in {'completed', 'cleared'}:
            action, reason, requires_user = 'create', 'Goal is ' + goal.status + '; create a new Goal or clear it', True
        elif goal.status == 'paused':
            action, reason, requires_user = 'user_resume', goal.reason or 'Goal is paused', True
        elif goal.status == 'waiting' and goal.wait_for in {'user', 'host'}:
            action = 'user_input' if goal.wait_for == 'user' else 'host_recovery'
            reason, requires_user = goal.reason or f'Goal waits for {goal.wait_for}', True
        else:
            execution, delivery = info.get('execution', {}), info.get('delivery', {})
            faults = info.get('runtime_faults', [])
            if faults:
                fault = faults[-1]
                action, source, reason, requires_user = (
                    'inspect_delivery' if fault['source'] == 'delivery' else 'inspect_execution',
                    fault['source'], fault['error'] or 'Inspect the original failure; do not replay blindly',
                    fault['source'] == 'delivery' or fault['status'] in {'interrupted', 'unknown'})
            elif delivery.get('status') == 'unknown':
                action, source, reason, requires_user = (
                    'inspect_delivery', 'delivery', 'Reminder outcome is unknown; do not replay blindly', True)
            elif execution.get('state') in {'unavailable', 'closing'}:
                action, source, reason = (
                    'await_owner_available', 'execution', 'Execution owner is unavailable or closing')
            elif for_agent:
                # The bound model tool is already executing on the main Agent.
                # Transport backpressure/other work is not an instruction to
                # await itself. Recorded lifecycle and uncertainty still win.
                if goal.status == 'waiting':
                    action, source, reason = 'inspect_tasks', 'goal', goal.reason or 'Inspect actual task evidence'
                else:
                    action, source, reason = ('main_agent_work', 'goal',
                        'Continue useful work in this turn under existing host permissions')
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
        owner = ('user' if requires_user else 'host' if source in {'execution', 'delivery'}
                 else 'task_workers' if goal is not None and goal.status == 'waiting' else 'main_agent')
        if action == 'host_recovery':
            owner = 'host'
        return {'action': action, 'owner': owner, 'source': source, 'reason': reason, 'requires_user': requires_user}

    async def _question(self, goal: Goal) -> dict[str, Any] | None:
        """Read-only correlation to actual host output facts; never sends or releases a wait."""
        if goal.status != 'waiting' or goal.wait_for != 'user':
            return None
        reference = await self.store.question_reference(goal)
        delivery: dict[str, Any] = {'status': 'unavailable', 'outputs': [],
                                    'reason': 'Host message delivery query is not available'}
        if reference is None:
            delivery['reason'] = 'Original question reference not recorded; delivery cannot be correlated'
        elif self.message_delivery is not None:
            def detached(value: dict[str, Any]) -> dict[str, Any]:
                if not isinstance(value, dict):
                    raise ValueError('Invalid host message delivery facts')
                value = dict(value)
                # Hosts may omit outputs when no correlation/reader exists. This
                # preserves untracked/unavailable, never proves nothing was sent.
                if value.get('status') in {'untracked', 'unavailable'}:
                    value.setdefault('outputs', [])
                if (value.get('status') not in {
                        'delivered', 'pending', 'unknown', 'partial', 'cancelled', 'untracked', 'unavailable'}
                        or not isinstance(value.get('outputs'), list)
                        or any(not isinstance(item, dict) for item in value['outputs'])
                        or value['status'] == 'delivered' and not value['outputs']):
                    raise ValueError('Invalid host message delivery facts')
                if value['status'] == 'delivered':
                    for output in value['outputs']:
                        text(output.get('id'))
                        if output.get('status') != 'delivered':
                            raise ValueError('Delivered aggregate requires delivered output receipts')
                return read_json(json_text(value, maximum=65536))
            self._check()
            try:
                async with asyncio.timeout(5):
                    facts = await self.message_delivery(reference)
                    delivery = await self._io(detached, facts)
            except OwnerRetired:
                raise
            except Exception:
                # Failure/absence is not an empty output journal or proof of not-sent.
                delivery = {'status': 'unavailable', 'outputs': [],
                            'reason': 'Host message delivery facts unavailable or invalid'}
            self._check()
        return {'id': reference, 'text': goal.question, 'impact': goal.impact, 'resume_when': goal.resume_when,
                'availability': 'recorded' if goal.question else 'not_recorded', 'delivery': delivery,
                'notice': 'Saving a question is not proof of delivery to the user'}

    async def summary(self, identifier: str = '', *, for_agent: bool = False) -> dict[str, Any]:
        """Native /goal status and model default: facts, counts, bounded previews.

        No plan body, full task bodies or report history. This is also exposed by
        status(detail=False); status() keeps the original detailed contract.
        latest_report is the Agent's recorded assessment, not a service verdict.
        Host execution/receipt facts stay independent of Goal eligibility. A stale
        deferred check is exposed as last_delivery when reminders are disabled.
        next is audience-aware display guidance, never authority or scheduling.
        """
        return await self.status(identifier, detail=False, for_agent=for_agent)

    async def status(self, identifier: str = '', *, detail: bool = True, for_agent: bool = False) -> dict[str, Any]:
        if type(detail) is not bool or type(for_agent) is not bool:
            raise ValueError('Goal status detail and for_agent must be booleans')
        async with self._lock:
            goal = await self._read_goal(identifier)
            if goal is None:
                info = self._status_info(goal)
                return {'format': 'xdog.goal_status.v2', 'current': None, **info,
                        **({'next': self._next_fact(None, info, for_agent=for_agent)} if not detail else {})}
            files = self.files(goal)
            value: dict[str, Any] = {'format': 'xdog.goal_status.v2', 'current': asdict(goal),
                                     'workspace': str(files.root), 'plan_path': str(files.root / 'plan.md'),
                                     'task_dir': str(files.tasks_dir),
                                     'last_transition': await self.store.last_transition(goal)}
            value['question'] = await self._question(goal)
            if value['question'] is not None:
                question = value['question']
                value['question_record'] = {
                    'id': question['id'], 'availability': question['availability'],
                    'delivery_status': question['delivery']['status'], 'notice': question['notice']}
            if detail:
                tasks = await self._io(files.list)
                value.update(tasks=await self._io(lambda: [asdict(task) for task in tasks]),
                             plan=await self._io(files.plan))
            else:
                value.update(await self._io(self._task_facts, files))
                value.update(report_path=str(files.progress_path),
                             latest_report=await self._io(files.latest_report))
            info = self._status_info(goal)  # Local host snapshots only; no endpoint/network probing.
            info['runtime_faults'] = (await self.store.wake_state(goal.id))['faults']
            if not detail:
                value['next'] = self._next_fact(goal, info, for_agent=for_agent)
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
