"""Native Goal extension: records, lifecycle signals and ACP reminders, never agent orchestration."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from typing import Any

from jsonschema import Draft202012Validator
from xdog.agent.async_work import run_owned, settle_cleanup, wait_owned
from xdog.agent.capabilities.children import CapabilityHost
from xdog.agent.capabilities.runtime import ParentPort
from xdog.agent.children import ChildSpec
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.agent.session_input import SessionInput, decode_input, input_data
from xdog.agent.tool_effects import ToolEffect

from .codec import json_text, read_json
from .data_pages import Page
from .host import OperationContext, OwnerRetired
from .models import Goal, Owner
from .presentation import format_change, format_history, format_status
from .service import GoalService
from .store import GoalStore, remindable
from .tools import _ACTIONS, create_goal_tool
from .ui_binding import GoalDisplay

logger = logging.getLogger(__name__)


class GoalRuntime:
    def __init__(self, host: CapabilityHost) -> None:
        self.host, self.loop = host, asyncio.get_running_loop()
        self.service: GoalService
        self._parent: ParentPort | None = None
        self._tool: AgentTool | None = None
        self._effect_validator: Draft202012Validator | None = None
        self._display: GoalDisplay | None = None
        self._remove_commands: Callable[[], None] = lambda: None
        self._unsubscribe: Callable[[], None] = lambda: None
        self._jobs: set[asyncio.Task[Any]] = set()
        self._job_errors: list[BaseException] = []
        self._observer: Any = None
        self._observer_key: tuple[str, str] | None = None
        self._refresh_lock = asyncio.Lock()
        self._wake_lock = asyncio.Lock()
        self._change_generation = 0
        self._controls = 0
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None
        self.automation: dict[str, Any] = {'status': 'idle'}
        self.delivery: dict[str, Any] = {'status': 'idle'}
        self._legacy_paths: tuple[str, ...] = ()

    @property
    def owner(self) -> Owner:
        suffix = ':' + self.host.branch if self.host.scope == 'session' else ''
        key = f'{self.host.owner_prefix}:{self.host.namespace}:{self.host.identity}{suffix}'
        return Owner(key, self.host.workspace)

    @classmethod
    async def open(cls, host: CapabilityHost) -> GoalRuntime:
        runtime = cls(host)
        if not host.valid():
            raise ValueError('Cannot bind a retired Goal owner')
        if host.scope == 'session':
            key = hashlib.sha256(runtime.owner.key.encode()).hexdigest()
            root = host.storage_root / 'goals' / key
        else:
            root = host.storage_root / 'goals'
        runtime._legacy_paths = await run_owned(lambda: tuple(str(path) for path in (
            root / 'journal.sqlite3', host.storage_root / 'goals.json') if os.path.lexists(path)))
        store = await GoalStore.open(root / 'registry.sqlite3', runtime.owner)
        runtime.service = GoalService(store, valid=runtime._valid, on_change=runtime._changed)
        runtime.service.info = lambda: {
            'automation': dict(runtime.automation), 'delivery': dict(runtime.delivery),
            'execution': runtime.host.execution_snapshot(), 'legacy_storage': list(runtime._legacy_paths)}
        try:
            runtime._unsubscribe = host.subscribe_events(runtime._event)
        except BaseException:
            await store.close()
            raise
        return runtime

    def _valid(self) -> bool:
        return (not self._closing and self.host.valid() and self.host.accepting()
                and self.owner == self.service.store.owner)

    def _current(self, parent: ParentPort) -> bool:
        return self._valid() and self._parent is parent and parent.valid() and self.host.owns_parent(parent)

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        return (self._tool,) if self._tool is not None else ()

    def tool_effect(self, actual_tool: AgentTool, args: dict[str, Any]) -> ToolEffect | None:
        """Classify current Goal data only, without IO or granting user authority."""
        parent = self._parent
        if (parent is None or actual_tool is not self._tool or not self._current(parent)
                or not isinstance(args, dict)):
            return None
        action = args.get('action')
        if (not isinstance(action, str) or action not in _ACTIONS
                or set(args) - {'action'} - _ACTIONS[action]):
            return None
        validator = self._effect_validator
        if validator is None or not validator.is_valid(args):
            return None
        required = {'create': {'title'}, 'update': {'version'}, 'activate': {'goal_id', 'version', 'reason'},
                    'report': {'report'}, 'task_update': {'task'}}
        if not required.get(action, set()) <= args.keys():
            return None
        read = action in {'status', 'summary', 'tasks', 'history'} or action == 'plan' and 'content' not in args
        if not read and set(args) & {'revision', 'offset', 'limit'}:
            return None
        try:
            Page.read(args)
        except ValueError:
            return None
        if read:
            return ToolEffect('read', 'tool_data')
        return ToolEffect('write', 'tool_data',
                          requires_approval=action not in {'plan', 'report', 'task_update', 'activate'})

    async def delegate_child_tool(self, actual_tool: AgentTool, spec: ChildSpec,
                                  scope: dict[str, Any]) -> AgentTool:
        """Narrow actual parent authority; the host still owns child admission and effects."""
        parent = self._parent
        if (asyncio.get_running_loop() is not self.loop or parent is None or actual_tool is not self._tool
                or not self._current(parent)):
            raise ValueError('Original Goal parent tool is unavailable for delegation')
        if (not isinstance(scope, dict) or set(scope) != {'goal_id', 'task_ids'}
                or not isinstance(scope.get('goal_id'), str) or not isinstance(scope.get('task_ids'), list)
                or not 1 <= len(scope['task_ids']) <= 128 or spec.tools is None or 'goal' not in spec.tools
                or spec.tool_scopes.get('goal') != scope):
            raise ValueError('Goal child scope requires the exact goal_id and explicit task_ids')
        from .child_tools import _task_io, create_child_goal_tool
        from .models import identity
        goal_id = identity(scope['goal_id'])
        task_ids = tuple(identity(value) for value in scope['task_ids'])
        if len(set(task_ids)) != len(task_ids):
            raise ValueError('Goal child task IDs must be distinct')
        def valid() -> bool:
            return (self._current(parent) and self._tool is actual_tool
                    and any(tool is actual_tool for tool in parent.agent.active_tools))
        with self.service.invocation(valid):
            async with self.service._lock:
                goal = await self.service._selected(goal_id)
                if goal.status not in {'active', 'waiting', 'paused'}:
                    raise ValueError('Goal child scope cannot target a completed/historical Goal')
                files = self.service.files(goal)
                for task_id in task_ids:
                    await self.service._io(_task_io, files, task_id, None, self.service._captured_check())
                self.service._check()
            return create_child_goal_tool(self.service, goal_id, task_ids, valid=valid)

    def attach(self, parent: ParentPort) -> None:
        if not self._valid() or not parent.valid() or not self.host.owns_parent(parent):
            raise ValueError('Goal parent owner is retired')
        previous = self._parent
        if previous is not None and previous.agent is parent.agent and previous.extensions is parent.extensions:
            if self._current(previous):
                return
        self._remove_commands()
        if self._display is not None:
            display, self._display = self._display, None
            display.retire()
            self._schedule(display.aclose)
        self._parent = parent
        def context(origin: str = 'model_tool') -> OperationContext:
            if not self._current(parent):
                raise ValueError('Goal tool owner is retired')
            return OperationContext(parent.user_authorized(), origin)
        original = create_goal_tool(self.service, context)
        self._effect_validator = Draft202012Validator(original.parameters)
        async def execute(*args: Any, **kwargs: Any) -> AgentToolResult:
            if asyncio.get_running_loop() is not self.loop or not self._current(parent):
                return AgentToolResult.from_text('Goal tool owner is retired or on another loop', is_error=True)
            assert original.execute is not None
            with self.service.invocation(lambda: self._current(parent)):
                result = await original.execute(*args, **kwargs)
            if not self._current(parent):
                return AgentToolResult.from_text('Goal owner changed during execution', is_error=True)
            await self._refresh()
            return result
        self._tool = replace(original, execute=execute)
        async def command(args: str) -> str:
            if asyncio.get_running_loop() is not self.loop or not self._current(parent):
                raise ValueError('Goal command owner is retired or on another loop')
            self._controls += 1
            try:
                with self.service.invocation(lambda: self._current(parent)):
                    return await self._command(args, context('native_command'))
            finally:
                self._controls -= 1
        self._remove_commands = parent.extensions.bind_native_commands('goal', {
            'goal': ('Current Goal: create/status/pause/resume/clear/history; main Agent plans and executes', command),
        }, valid=lambda: self._current(parent), shadow_skills={'goal'})
        if 'goal_ui' in parent.extensions.enabled:
            self._display = GoalDisplay(self.service, parent.extensions.views,
                valid=lambda: self._current(parent), notify=parent.extensions.notify_view_changed)
        self._changed()

    def _schedule(self, operation: Callable[[], Coroutine[Any, Any, Any]]) -> None:
        if self._closing or self.loop.is_closed():
            return
        def admit() -> None:
            if self._closing:
                return
            async def run() -> Any:
                if not self._valid():
                    return None
                try:
                    return await operation()
                except OwnerRetired:
                    if self._valid():
                        raise  # A stale invocation is not a generic host retirement.
                    return None
            task = self.loop.create_task(run())
            self._jobs.add(task)
            def done(result: asyncio.Task[Any]) -> None:
                self._jobs.discard(result)
                if not result.cancelled() and result.exception() is not None:
                    error = result.exception()
                    assert error is not None
                    self._job_errors.append(error)
                    self.automation = {'status': 'error', 'error': str(error)}
                    logger.warning('Goal extension obligation failed: %s', result.exception())
            task.add_done_callback(done)
        self.loop.call_soon_threadsafe(admit)

    def _changed(self) -> None:
        self._change_generation += 1  # Invalidates a read that straddles a committed lifecycle change.
        if self._display is not None:
            self._display.changed()
        self._schedule(self._refresh)

    def _event(self, event: dict[str, Any]) -> None:
        if not self._valid():
            return
        kind = event.get('type')
        if kind == 'owner.retiring':
            self._schedule(self._stop_observer)
        elif kind == 'execution.settled':
            if self._display is not None:
                self._display.changed()
            identifier = str(event.get('execution_id', ''))
            status = event.get('status')
            if not identifier:
                return
            self._schedule(lambda: self._settled(identifier, str(status)))
        elif kind == 'owner.available':
            # Availability is not authorization to replay interrupted effects.
            self._schedule(self._refresh)
            self._schedule(self._available)

    async def _settled(self, identifier: str, status: str, *, expected: Goal | None = None) -> None:
        if not self._valid():
            return
        if status in {'failed', 'interrupted', 'cancelled', 'canceled', 'preparation_failed'}:
            await self._host_wait(status, expected=expected)
        elif status == 'succeeded':
            goal = await self.service.current()
            if goal is not None and goal.status == 'active' and (expected is None or goal == expected):
                await self._wake('execution:' + identifier)

    async def _available(self) -> None:
        """Reconcile persisted facts, never infer execution from idle time."""
        if not self._valid():
            return
        goal = await self.service.current()
        if goal is None or not remindable(goal):
            return
        latest = getattr(self.host, 'latest_execution', None)
        if latest is not None:
            try:
                fact = await latest()
            except (RuntimeError, ValueError):
                if not self._valid() or self.host.execution_snapshot().get('state') == 'closing':
                    return  # Normal owner retirement is not a failed storage obligation.
                raise
            if not self._valid():
                return
            if fact is not None and fact.get('availability') == 'unavailable':
                self.delivery = {'status': 'unavailable', 'reason': fact['reason']}
                await self._wake(None)  # Existing durable causes/outbox remain usable.
                return
            wake = await self.service.store.wake_state(goal.id)
            if fact is not None and fact.get('settled_at', 0) >= wake['epoch_time']:
                await self._settled(fact['execution_id'], fact['status'], expected=goal)
                await self._wake(None)
                return
        await self._wake(None)  # Original pending causes/outbox survive a new process.

    async def _host_wait(self, reason: str, *, expected: Goal | None = None) -> None:
        if not self._valid():
            return
        generation = self._change_generation
        goal = await self.service.current()
        if expected is not None and goal != expected:
            return  # The original fact cannot put a newer Goal/continuation into host wait.
        if goal is not None and remindable(goal):
            try:
                # Fence both service-lock admission and the actual database commit.
                # End the pin before refreshing our own newly committed wait.
                with self.service.invocation(lambda: generation == self._change_generation):
                    await self.service.change('wait', goal.id, version=goal.version,
                                              reason='Host execution ' + reason, until='host',
                                              context=OperationContext(origin='runtime'))
            except OwnerRetired:
                if not self._valid() or generation == self._change_generation:
                    raise
                return  # A concurrent lifecycle change revoked only this original fact.
        await self._refresh()

    async def _stop_observer(self) -> None:
        observer, self._observer = self._observer, None
        self._observer_key = None
        if observer is not None:
            await observer.aclose()

    async def _refresh(self) -> None:
        async with self._refresh_lock:
            if not self._valid():
                return
            goal = await self.service.current()
            if goal is None or goal.status not in {'active', 'waiting'} or (
                    goal.status == 'waiting' and goal.wait_for != 'tasks'):
                await self._stop_observer()
                self.automation = {'status': 'paused' if goal is not None and goal.status == 'paused' else 'idle'}
                return
            target = await self.host.acp_target()
            if target is None:
                await self._stop_observer()
                self.automation = {'status': 'unavailable', 'reason': 'No active owner ACP endpoint'}
                return
            key = goal.id, json_text(target)
            if self._observer_key == key:
                return
            await self._stop_observer()
            from .automation import TaskObserver
            observer = TaskObserver()
            files = self.service.files(goal)
            try:
                result = await observer.start(target, files.tasks_dir, files.root / 'eyes.json', goal.id,
                                              lambda delivery: self._task_changed(goal.id, delivery))
            except BaseException:
                await observer.aclose()
                raise
            if not self._valid():
                await observer.aclose()
                return
            self.automation = result
            if result.get('status') == 'watching':
                self._observer, self._observer_key = observer, key
            else:
                await observer.aclose()
                self._observer, self._observer_key = None, None
            # Observation baselines do not emit historical changes. Reconcile a
            # bounded authoritative snapshot to notice edits made while paused/offline.
            await self._capture_task_changes(goal)

    async def _capture_task_changes(self, goal: Goal) -> None:
        def fingerprint() -> tuple[str, int]:
            digest = hashlib.sha256()
            tasks = self.service.files(goal).list()
            for task in tasks:
                digest.update(json_text(asdict(task), maximum=None).encode())
                digest.update(b'\n')
            return digest.hexdigest(), len(tasks)
        digest, count = await self.service._io(fingerprint)
        if not self._valid() or await self.service.current() != goal:
            return
        wake = await self.service.store.wake_state(goal.id)
        previous = wake['manifest']
        if (not previous and not count) or previous.rsplit(':', 1)[-1] == digest:
            return
        if await self.service.store.signal(goal, f"task-snapshot:{wake['sequence']}:{digest}"):
            self._schedule(lambda: self._wake(None))

    async def _task_changed(self, goal_id: str, delivery: Any) -> None:
        """Eyes commits detection into this Goal's coalescer, not a second inbox sender."""
        if not self._valid():
            return
        goal = await self.service.current()
        if goal is None or goal.id != goal_id or not remindable(goal):
            return
        if await self.service.store.signal(goal, 'tasks:' + str(delivery.id)):
            self._schedule(lambda: self._wake(None))

    async def _outstanding(self, target: dict[str, Any],
                           rows: list[dict[str, Any]], epoch: int) -> bool:
        """Accepted is storage ACK, not completion. Inspect the original receipt."""
        for row in rows:
            if row['status'] == 'unknown':
                self.delivery = {'status': 'unknown', 'message_id': row['id'], 'error': row['error']}
                await self._host_wait('reminder delivery uncertain; inspect original receipt')
                return True
            if row['status'] != 'accepted':
                continue
            try:
                from xdog.agent.acp.client import inbox_status
                value = await inbox_status(target, row['id'])
            except Exception as exc:
                self.delivery = {'status': 'unavailable', 'message_id': row['id'], 'error': str(exc)}
                return True
            if not self._valid():
                return True
            messages = value['messages']
            if len(messages) != 1:
                self.delivery = {'status': 'unknown', 'message_id': row['id'], 'error': 'Original receipt missing'}
                await self._host_wait('Original accepted reminder receipt missing; inspect before resuming')
                return True
            state = messages[0].get('status')
            if state not in {'pending', 'claimed', 'running', 'succeeded', 'failed', 'interrupted',
                             'cancelled', 'preparation_failed', 'pending_review', 'approved'}:
                raise ValueError('Invalid original reminder execution receipt')
            self.delivery = {'status': state, 'message_id': row['id']}
            if state in {'failed', 'interrupted', 'cancelled', 'preparation_failed'}:
                if read_json(row['payload']).get('provenance', {}).get('goal_version', 1) < epoch:
                    continue  # Explicit recovery may continue, but an old pending send still backpressures.
                await self._host_wait('Original reminder execution ' + state)
                return True
            if state != 'succeeded':
                return True
        return False

    @asynccontextmanager
    async def _recheck_changed_wake(self) -> AsyncIterator[None]:
        generation = self._change_generation
        try:
            yield
        finally:
            if self._valid() and generation != self._change_generation:
                # Revisit durable causes once after an in-flight wake was invalidated.
                # Unlike waking on every _changed(), this does not create causes or
                # spin on refresh/report callbacks. Receipt coalescing remains authoritative.
                self._schedule(lambda: self._wake(None))

    async def _wake(self, trigger: str | None) -> None:
        async with self._wake_lock, self._recheck_changed_wake():
            if not self._valid():
                return
            goal = await self.service.current()
            if goal is None or not remindable(goal):
                return
            if trigger is not None and not await self.service.store.signal(goal, trigger):
                return
            wake = await self.service.store.wake_state(goal.id)
            rows = wake['reminders']
            accepted = [row for row in rows if row['status'] == 'accepted']
            through = max((read_json(row['payload']).get('provenance', {}).get('wake_through', 0)
                           for row in accepted), default=0)
            retry = next((row for row in rows if row['status'] in {'pending', 'unavailable'}), None)
            # Inspect unknown even when there is no new cause. Never replace it with a new ID.
            uncertain = any(row['status'] == 'unknown' for row in rows)
            if (goal.status == 'waiting' and goal.wait_for == 'tasks'
                    and wake['task_sequence'] <= through and not uncertain):
                return  # Ordinary settlement/availability is not a task-change wakeup.
            if not rows and not wake['sequence']:
                return
            state = self.host.execution_snapshot()
            if (self._controls or state.get('state') != 'idle' or state.get('user_pending') is not False
                    or state.get('children_active') is not False):
                self.delivery = {'status': 'deferred', 'reason': 'Owner busy, user input pending or children active'}
                return
            target = await self.host.acp_target()
            if target is None:
                self.delivery = {'status': 'unavailable', 'reason': 'ACP reminder endpoint unavailable'}
                return
            # Check only the most recent accepted receipt; older terminal receipts do not need rescanning.
            check = [row for row in rows if row['status'] == 'unknown'] + wake['accepted'][-1:]
            if await self._outstanding(target, check, wake['epoch']):
                return
            if wake['sequence'] <= through and retry is None and not uncertain:
                return
            if await self.service.current() != goal or not self._valid():
                return
            if retry is not None:
                data = read_json(retry['payload'])
                if data.get('provenance', {}).get('goal_version') != goal.version:
                    await self.service.store.cancel_pending(retry['id'])
                    retry = None
            if retry is None:
                identifier = 'goal-' + hashlib.sha256(json_text(
                    [self.owner.key, goal.id, goal.version, wake['sequence']]).encode()).hexdigest()
                files = self.service.files(goal)
                message = SessionInput(identifier, 'automatic', 'goal',
                    f'Goal reminder for {goal.id}. Follow the already-loaded Goal skill; '
                    'read current Goal status first. '
                    f'Goal workspace: {files.root}. Continue only if this is still your current Goal and '
                    'is active (or waiting for tasks whose changes you must inspect before resuming). '
                    'If paused, completed, cleared or waiting for user/host, do not continue or release that state. '
                    'For a tasks wait, inspect the changed tasks and use goal.activate with the exact goal_id, '
                    'observed version and reason only when you judge the wait condition satisfied. '
                    'Before doing more work, compare actual results against the previous progress report, '
                    'then call goal(action="report") with progress, completed work, evidence, blockers and next steps. '
                    'The first report establishes a baseline. Repeated lack of progress without a viable new approach '
                    'should lead you to goal.pause with the blocker and resume conditions, not a blind loop. '
                    'You choose the plan, tasks, tools and evidence-based completion. No user permissions are granted. '
                    'If waiting for approval, record goal.wait(until="user") and ask; do not retry approvals.', (),
                    {'goal_id': goal.id, 'goal_version': goal.version, 'wake_through': wake['sequence'],
                     'triggers': wake['causes']})
                receipt = await self.service.store.reminder(goal, input_data(message))
                if receipt['status'] not in {'pending', 'unavailable'}:
                    return
            else:
                identifier = retry['id']
                message = decode_input(read_json(retry['payload']))
            if not await self.service.store.claim_reminder(identifier, goal):
                return
            # A local outbox claim is not a remote send. Recheck after its IO
            # admission so pause/retirement cannot initiate a transport afterward.
            generation = self._change_generation
            current = await self.service.store.current()
            if not self._valid() or generation != self._change_generation or current != goal:
                await self.service.store.settle_reminder(identifier, 'unavailable',
                    'Nothing sent: original Goal/owner changed before transport')
                return
            state = self.host.execution_snapshot()
            if (self._controls or state.get('state') != 'idle' or state.get('user_pending') is not False
                    or state.get('children_active') is not False):
                await self.service.store.settle_reminder(identifier, 'unavailable',
                    'Nothing sent: owner became busy before transport')
                self.delivery = {'status': 'deferred', 'message_id': identifier}
                return
            try:
                from xdog.agent.acp.client import enqueue
                result = await enqueue(target, message)
            except BaseException as exc:
                await self.service.store.settle_reminder(identifier, 'unknown', str(exc))
                if self._valid():
                    await self._host_wait('reminder delivery uncertain; inspect original receipt')
                raise
            status = result.get('status')
            if status == 'unavailable':
                await self.service.store.settle_reminder(identifier, 'unavailable', json_text(result))
                self.delivery = {'status': 'unavailable', 'message_id': identifier, 'receipt': result}
                return
            if status not in {'accepted', 'duplicate', 'queued'}:
                await self.service.store.settle_reminder(identifier, 'unknown', json_text(result))
                await self._host_wait('reminder delivery uncertain; inspect original receipt')
                self.delivery = {'status': 'unknown', 'message_id': identifier, 'receipt': result}
                self.automation = dict(self.delivery)
                return
            await self.service.store.settle_reminder(identifier, 'accepted')
            self.delivery = {'status': result.get('message_state', 'accepted'), 'message_id': identifier,
                             'notice': 'Accepted by Inbox, not proof of execution or completion'}

    async def _command(self, args: str, context: OperationContext) -> str:
        action, _, rest = args.strip().partition(' ')
        action = action or 'status'
        if action in {'status', '--json'}:
            if ((action == '--json' and rest.strip())
                    or (action == 'status' and rest.strip() not in {'', '--json'})):
                raise ValueError('Usage: /goal status [--json]')
            facts = await self.service.summary()
            if action == '--json' or rest.strip() == '--json':
                return json_text(facts, maximum=1024 * 1024, ensure_ascii=False)
            return format_status(facts)
        if action == 'history':
            parts = rest.split()
            raw = '--json' in parts
            if raw:
                parts.remove('--json')
            if len(parts) > 1 or parts and (not parts[0].isdecimal() or len(parts[0]) > 12):
                raise ValueError('Usage: /goal history [OFFSET] [--json]')
            offset = int(parts[0]) if parts else 0
            goals = await self.service.list()
            facts = {'goals': [{key: asdict(goal)[key] for key in ('id', 'title', 'status', 'version')}
                               for goal in goals[offset:offset + 16]],
                     'total': len(goals), 'next_offset': offset + 16 if offset + 16 < len(goals) else None,
                     'legacy_storage': list(self._legacy_paths),
                     'legacy_note': 'Legacy stores retained unchanged; no old workers/reviewers are resumed'}
            return json_text(facts, ensure_ascii=False) if raw else format_history(facts)
        if action == 'create':
            if not rest.strip():
                raise ValueError('Usage: /goal create <outcome>; the current main Agent plans it')
            goal = await self.service.create(rest.strip()[:160], rest.strip(), context=context)
        elif action in {'pause', 'resume', 'clear'}:
            if action == 'resume' and rest.strip():
                raise ValueError('Usage: /goal resume')
            fields: dict[str, Any] = {'reason': rest.strip()} if action != 'resume' else {}
            goal = await self.service.change(action, context=context, **fields)
        else:
            raise ValueError('Usage: /goal [create <outcome>|status|pause [reason]|resume|clear [reason]|history]')
        await self._refresh()
        if action in {'create', 'resume'}:
            self._schedule(lambda: self._wake(None))
        return format_change(action, {'goal': asdict(goal), 'automation': self.automation,
                          'last_transition': await self.service.store.last_transition(goal),
                          'notice': ('Automatic continuation and owned Eyes observation are stopped; accepted input, '
                                     'running tools and children are not cancelled' if action == 'pause' else
                                     'The main Agent must inspect current tasks and report progress before working'),
                          'next': 'The current main Agent follows the goal skill; no worker was started here'})

    async def _settle_jobs(self) -> None:
        errors: list[BaseException] = []
        await asyncio.sleep(0)
        while self._jobs:
            results = await asyncio.gather(*tuple(self._jobs), return_exceptions=True)
            errors.extend(item for item in results if isinstance(item, BaseException)
                          and not isinstance(item, asyncio.CancelledError))
            await asyncio.sleep(0)
        unique = {id(error): error for error in [*self._job_errors, *errors]}
        if len(unique) == 1:
            raise next(iter(unique.values()))
        if unique:
            raise BaseExceptionGroup('Goal extension obligations failed', list(unique.values()))

    async def flush(self) -> None:
        await self._settle_jobs()

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closing = True
            async def detach() -> None:
                failures: list[BaseException] = []
                for remove in (self._unsubscribe, self._remove_commands):
                    try:
                        remove()
                    except BaseException as exc:
                        failures.append(exc)
                if failures:
                    raise BaseExceptionGroup('Goal extension detach failed', failures)
            async def jobs() -> None:
                await self._settle_jobs()
            async def display() -> None:
                if self._display is not None:
                    await self._display.aclose()
            async def close() -> None:
                try:
                    await settle_cleanup(detach, jobs, self._stop_observer, display, self.service.close,
                                         message='Goal extension cleanup failed')
                finally:
                    self._parent = None
                    self._tool = None
                    self._display = None
            self._close_task = asyncio.create_task(close())
        await wait_owned(self._close_task)


class GoalProvider:
    name = 'goal'
    scopes = ('session', 'group')
    tool_names = ('goal',)
    command_names = ('goal',)
    default_skills = ('goal',)
    requirements: tuple[str, ...] = ()
    optional_requirements = ('acp',)

    async def open(self, host: CapabilityHost) -> GoalRuntime:
        return await GoalRuntime.open(host)


provider = GoalProvider()
