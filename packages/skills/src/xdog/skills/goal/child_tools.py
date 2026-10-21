"""Explicit, owner-bound task delegation; never a Goal lifecycle or worker API.

The host/provider must bind the actual tool identity, grant and original owner
loop. This factory only narrows that grant, and creates no authority or worker.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, replace
from typing import Any

from xdog.agent.core import AgentTool, AgentToolResult
from xdog.agent.tool_effects import ToolEffect

from .codec import decode_task, encode_task, json_text
from .data_pages import PAGE_BYTES, Page, response, snapshot_revision
from .files import TaskFiles, _locked, _read, _write, directory
from .host import OwnerRetired
from .models import TASK_STATES, Task, identity, number, text
from .service import GoalService

_READ_FIELDS = {'action', 'goal_id', 'task_id', 'revision', 'offset', 'limit'}
_WRITE_FIELDS = {'action', 'goal_id', 'task'}
_CHANGES = {'status', 'summary', 'evidence'}


def _validate(args: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None, Page]:
    """Validate independently of provider schema validation, without IO/authority."""
    if not isinstance(args, dict):
        raise ValueError('Expected child Goal arguments')
    if 'goal_id' in args:
        identity(args['goal_id'])
    action = args.get('action')
    if action == 'tasks':
        if set(args) - _READ_FIELDS:
            raise ValueError('Child tasks requires only one task_id and optional data page fields')
        return action, identity(args.get('task_id')), None, Page.read(args)
    if action != 'task_update' or set(args) - _WRITE_FIELDS:
        raise ValueError('Child Goal only supports tasks and task_update')
    task = args.get('task')
    if not isinstance(task, dict) or set(task) - {'id', 'expected_version', *_CHANGES}:
        raise ValueError('Child task_update only permits id, expected_version, status, summary and evidence')
    task_id = identity(task.get('id'))
    number(task.get('expected_version'))  # Strict positive CAS: no bool/float or create(0).
    if not set(task) & _CHANGES:
        raise ValueError('Child task_update requires at least one result field')
    data = dict(task)
    if 'status' in data and (not isinstance(data['status'], str) or data['status'] not in TASK_STATES):
        raise ValueError('Invalid task status')
    if 'summary' in data:
        text(data['summary'], empty=True)
    if 'evidence' in data:
        if not isinstance(data['evidence'], list):
            raise ValueError('Expected task evidence array')
        data['evidence'] = tuple(text(item) for item in data['evidence'])
    return action, task_id, data, Page()


def child_tool_effect(args: dict[str, Any]) -> ToolEffect | None:
    """Pure shape classification, NOT proof of tool identity, scope or permission.

    The host/provider must recognize its actual binding before using this result;
    readonly/ask and all admission policy remain with the original host gate.
    """
    try:
        action, _, _, _ = _validate(args)
    except (ValueError, TypeError):
        return None
    return ToolEffect('read' if action == 'tasks' else 'write', 'tool_data')


def _task_io(files: TaskFiles, task_id: str, data: dict[str, Any] | None,
             check: Callable[[], None]) -> Task:
    """Single-file TaskFiles operation on its existing lock/CAS/atomic IO format.

    TaskFiles.update scans every task. Use its same .task-write.lock, codecs and
    atomic write primitives instead, so a child never reads an ungranted task.
    Recheck after acquiring the OS lock (which can block behind an external CLI)
    and immediately before the atomic write. No separate storage/lock protocol.
    """
    def selected() -> Task:
        check()
        with directory(files.tasks_dir) as fd:
            try:
                current = decode_task(_read(fd, task_id + '.json'))
            except FileNotFoundError:
                raise ValueError('Unknown granted task; children cannot create tasks') from None
            if current.id != task_id:
                raise ValueError('Task filename and identity disagree')
            if data is None:
                check()
                return current
            expected = data['expected_version']
            if current.version != expected:
                raise ValueError('Task version conflict; re-read before updating')
            changes = {key: value for key, value in data.items() if key in _CHANGES}
            updated = replace(current, **changes, version=expected + 1)
            payload = encode_task(updated)
            check()
            _write(fd, task_id + '.json', payload)
            return updated

    check()
    if data is None:
        return selected()
    with directory(files.root) as root, _locked(root, '.task-write.lock'):
        return selected()


def create_child_goal_tool(service: GoalService, goal_id: str, task_ids: tuple[str, ...], *,
                           valid: Callable[[], bool]) -> AgentTool:
    """Bind an explicitly granted current Goal and existing tasks on its owner loop.

    ``valid`` must fence the original child/parent grant, not Goal pause state.
    It is also checked on owned IO workers, like GoalService invocation fences.
    Paused/waiting Goals accept original child results without releasing waits.
    Completed/cleared/replaced Goals and retired owners permit no historical IO.
    """
    identity(goal_id)
    if type(task_ids) is not tuple or not task_ids:
        raise ValueError('Child Goal requires explicit unique task IDs')
    for task_id in task_ids:
        identity(task_id)
    if len(set(task_ids)) != len(task_ids):
        raise ValueError('Child Goal requires explicit unique task IDs')
    if not callable(valid):
        raise ValueError('Child Goal requires its original invocation fence')
    granted = frozenset(task_ids)
    store, owner = service.store, service.store.owner
    loop = asyncio.get_running_loop()
    properties: dict[str, Any] = {
        'action': {'type': 'string', 'enum': ['tasks', 'task_update']},
        'goal_id': {'type': 'string', 'enum': [goal_id]},
        'task_id': {'type': 'string', 'enum': list(task_ids)},
        'revision': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
        'offset': {'type': 'integer', 'minimum': 0},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': PAGE_BYTES},
        'task': {'type': 'object', 'additionalProperties': False,
                 'required': ['id', 'expected_version'],
                 'anyOf': [{'required': [key]} for key in sorted(_CHANGES)],
                 'properties': {
                     'id': {'type': 'string', 'enum': list(task_ids)},
                     'expected_version': {'type': 'integer', 'minimum': 1, 'maximum': 2**53 - 1},
                     'status': {'type': 'string', 'enum': sorted(TASK_STATES)},
                     'summary': {'type': 'string'},
                     'evidence': {'type': 'array', 'items': {'type': 'string'}},
                 }},
    }
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['action'],
              'properties': properties,
              'anyOf': [
                  {'properties': {'action': {'enum': ['tasks']}, 'task': False}, 'required': ['task_id']},
                  {'properties': {'action': {'enum': ['task_update']},
                                  **{key: False for key in _READ_FIELDS - _WRITE_FIELDS}},
                   'required': ['task']},
              ]}

    async def execute(call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                      on_update: Any = None, ctx: Any = None) -> AgentToolResult:
        def invocation_valid() -> bool:
            if cancel is not None and cancel.is_set():
                raise asyncio.CancelledError
            return (service.store is store and store.owner == owner and valid())

        try:
            if asyncio.get_running_loop() is not loop:
                raise OwnerRetired('Child Goal must execute on its original owner loop')
            with service.invocation(invocation_valid):
                action, task_id, data, page = _validate(params)
                if params.get('goal_id', goal_id) != goal_id or task_id not in granted:
                    raise ValueError('Task/Goal is outside the original child grant')
                # One lock covers current selection, actual file IO and publication.
                # Lifecycle mutations cannot retire/switch the Goal during owned IO.
                async with service._lock:
                    goal = await service._selected(goal_id)
                    if goal.status not in {'active', 'waiting', 'paused'}:
                        raise ValueError('Child Goal is no longer executable')
                    task = await service._io(_task_io, service.files(goal), task_id, data,
                                             service._captured_check())
                    if data is not None:
                        service._check()
                        service.on_change()  # Facts changed; never resume/release a pause or wait.
                    value = (asdict(task) if data is not None else {
                        'format': 'xdog.goal_tasks.v1', 'goal_id': goal_id,
                        'task_dir': str(service.files(goal).tasks_dir), 'tasks': [asdict(task)]})
                    selection: dict[str, object] = {'action': action, 'goal_id': goal_id, **(
                        {'task': data} if data is not None else {'task_id': task_id})}
                    revision = await service._io(snapshot_revision, value, selection)
                    if 'revision' in params and params['revision'] != revision:
                        raise ValueError('Goal/task snapshot changed; restart reading')
                    rendered = await service._io(response, lambda: value,
                        {'kind': action, 'revision': revision}, {**selection, 'revision': revision},
                        page, committed=data is not None)
                    service._check()
                    return AgentToolResult.from_text(rendered)
        except (ValueError, KeyError, TypeError) as exc:
            return AgentToolResult.from_text(json_text({'error': str(exc)}, ensure_ascii=False), is_error=True)
        # Cancellation and real IO/storage errors propagate, never fake success.

    return AgentTool('goal', 'Read one explicitly granted task using tasks(task_id), or update its existing '
                     'status/summary/evidence with task_update(task={id,expected_version,...}). '
                     'No task creation, assignment, plan, report or Goal lifecycle authority. '
                     'Use next_query unchanged for data pages.', schema, execute=execute)
