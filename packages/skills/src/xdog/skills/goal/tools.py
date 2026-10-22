"""One native extension tool. Plans and completion are supplied by its main Agent."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from xdog.agent.async_work import run_owned
from xdog.agent.core import AgentTool, AgentToolResult

from .codec import json_text
from .data_pages import Page, response, snapshot_revision
from .host import OperationContext
from .service import GoalService

_ACTIONS = {
    'create': {'title', 'outcome', 'criteria'},
    'status': {'goal_id', 'detail', 'revision', 'offset', 'limit'},
    'summary': {'goal_id', 'revision', 'offset', 'limit'},
    'tasks': {'goal_id', 'task_id', 'status', 'worker', 'revision', 'offset', 'limit'},
    'history': {'goal_id', 'revision', 'offset', 'limit'},
    'update': {'goal_id', 'version', 'title', 'outcome', 'criteria'},
    'plan': {'goal_id', 'content', 'revision', 'offset', 'limit'},
    'report': {'goal_id', 'report'},
    'task_update': {'goal_id', 'task'},
    'wait': {'goal_id', 'reason', 'until', 'question', 'impact', 'resume_when'},
    'pause': {'goal_id', 'reason'},
    'activate': {'goal_id', 'version', 'reason'},
    'resume': {'goal_id'},
    'complete': {'goal_id', 'summary'},
    'clear': {'goal_id', 'reason'},
}


class _ArgumentError(ValueError):
    def __init__(self, code: str, action: str, fields: list[str], message: str) -> None:
        super().__init__(message)
        self.data = {'code': code, 'action': action, 'fields': fields}


def _validate(params: dict[str, Any], properties: dict[str, Any]) -> str:
    """Action-specific fields first; then exact required paths and strict JSON types."""
    action = params.get('action')
    if 'action' not in params:
        raise _ArgumentError('missing_required', '', ['action'], 'Missing required field: action')
    if not isinstance(action, str):
        raise _ArgumentError('type', '', ['action'], 'action must be a string')
    if action not in _ACTIONS:
        raise _ArgumentError('unknown_action', action, ['action'], f'Unknown Goal action: {action}')
    unsupported = sorted(set(params) - {'action'} - _ACTIONS[action])
    if unsupported:
        message = f'{action} does not accept fields: {", ".join(unsupported)}'
        if action == 'update':
            message += '; accepts goal_id (optional), version, title/outcome/criteria; reason is not accepted'
        raise _ArgumentError('unsupported_fields', action, unsupported, message)
    if action == 'wait' and params.get('until', 'user') == 'host':
        raise _ArgumentError('invalid_value', action, ['until'],
                             'wait(host) is no longer supported; record capability blockers without a host wait')
    if action == 'wait' and params.get('until', 'user') != 'user':
        extra = sorted(set(params) & {'question', 'impact', 'resume_when'})
        if extra:
            raise _ArgumentError('unsupported_fields', action, extra,
                                 'Only wait(user) accepts question/impact/resume_when')
    required = {'create': ['title'], 'task_update': ['task'], 'report': ['report'],
                'update': ['version'], 'activate': ['goal_id', 'version', 'reason'],
                'complete': ['summary']}.get(action, [])
    if action == 'wait' and params.get('until', 'user') == 'user':
        required = ['question', 'impact', 'resume_when']
    missing = [key for key in required if key not in params]
    if missing:
        raise _ArgumentError('missing_required', action, missing,
                             f'{action} missing required fields: {", ".join(missing)}')

    def check(value: Any, schema: dict[str, Any], path: str) -> None:
        kind = schema['type']
        valid = (isinstance(value, str) if kind == 'string' else type(value) is int if kind == 'integer'
                 else type(value) is bool if kind == 'boolean' else isinstance(value, list) if kind == 'array'
                 else isinstance(value, dict))
        if not valid:
            raise _ArgumentError('type', action, [path], f'{action}.{path} must be {kind}')
        if kind == 'object':
            extra = sorted(set(value) - set(schema['properties']))
            if extra:
                paths = [path + '.' + key for key in extra]
                raise _ArgumentError('unsupported_fields', action, paths,
                                     f'{action} does not accept fields: {", ".join(paths)}')
            missing = [path + '.' + key for key in schema.get('required', []) if key not in value]
            if missing:
                raise _ArgumentError('missing_required', action, missing,
                                     f'{action} missing required fields: {", ".join(missing)}')
            for key, item in value.items():
                check(item, schema['properties'][key], path + '.' + key)
        elif kind == 'array':
            for index, item in enumerate(value):
                check(item, schema['items'], f'{path}[{index}]')

    for key, value in params.items():
        check(value, properties[key], key)
    if action == 'task_update' and params['task'].get('expected_version') == 0 and 'title' not in params['task']:
        raise _ArgumentError('missing_required', action, ['task.title'],
                             'task_update create missing required field: task.title')
    if action == 'update' and not set(params) & {'title', 'outcome', 'criteria'}:
        raise _ArgumentError('missing_required', action, ['title', 'outcome', 'criteria'],
                             'update requires at least one of title/outcome/criteria; reason is not accepted')
    return action


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError('Expected an array of strings')
    return tuple(value)


def create_goal_tool(service: GoalService, context: Callable[[], OperationContext]) -> AgentTool:
    properties: dict[str, Any] = {'action': {'type': 'string', 'enum': list(_ACTIONS)}}
    for key in set().union(*_ACTIONS.values()) - {'task', 'report'}:
        properties[key] = ({'type': 'array', 'items': {'type': 'string'}} if key == 'criteria' else
                           {'type': 'integer'} if key in {'version', 'offset', 'limit'} else
                           {'type': 'boolean'} if key == 'detail' else {'type': 'string'})
    task_properties = {name: ({'type': 'array', 'items': {'type': 'string'}}
                              if name in {'depends_on', 'evidence'} else {'type': 'integer'}
                              if name == 'expected_version' else {'type': 'string'}) for name in
                       ('id', 'title', 'description', 'status', 'worker', 'depends_on', 'summary', 'evidence',
                        'expected_version')}
    properties['task'] = {'type': 'object', 'required': ['id', 'expected_version'],
                          'additionalProperties': False, 'properties': task_properties}

    report_fields = ('expected_version', 'progress', 'completed', 'evidence', 'blockers', 'next_steps', 'summary')
    properties['report'] = {'type': 'object', 'required': list(report_fields), 'additionalProperties': False,
        'properties': {name: ({'type': 'integer', 'minimum': 0} if name == 'expected_version' else
                             {'type': 'string', 'enum': ['baseline', 'advanced', 'unchanged', 'regressed']}
                             if name == 'progress' else {'type': 'string'} if name == 'summary' else
                             {'type': 'array', 'items': {'type': 'string'}}) for name in report_fields}}
    for name in ('question', 'impact', 'resume_when'):
        properties[name]['description'] = (
            'Required nonempty text for wait(until=user), omitted until defaults to user. '
            'Saved atomically as data, not proof of user delivery or permission.')
    properties['until']['enum'] = ['user', 'tasks']
    properties['until']['description'] = 'wait only: user decision/material or task evidence; never host faults.'
    properties['detail']['description'] = 'status only: opt into legacy full tasks and plan; default false.'
    properties['status']['description'] = 'tasks only: exact task status filter.'
    properties['content']['description'] = 'plan: supply to write; omit to read only the plan.'
    properties['offset']['description'] = 'Byte offset in the JSON snapshot; use next_query unchanged.'

    async def execute(call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                      on_update: Any = None, ctx: Any = None) -> AgentToolResult:
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        try:
            action = _validate(params, properties)
            identifier = params.get('goal_id', '')
            if not isinstance(identifier, str):
                raise ValueError('Invalid Goal identity')
            committed = action not in {'status', 'summary', 'tasks', 'history'} and not (
                action == 'plan' and 'content' not in params)
            if committed and set(params) & {'revision', 'offset', 'limit'}:
                raise ValueError('Only read actions accept data page fields')
            page = Page.read(params)
            service._check()
            if action == 'create':
                value: Any = asdict(await service.create(params['title'], params.get('outcome', params['title']),
                    _strings(params.get('criteria', [])), context=context()))
            elif action == 'status':
                value = await service.status(identifier, detail=params.get('detail', False), for_agent=True)
            elif action == 'summary':
                value = await service.summary(identifier, for_agent=True)
            elif action == 'tasks':
                value = await service.tasks(identifier, task_id=params.get('task_id', ''),
                                            status=params.get('status', ''), worker=params.get('worker', ''))
            elif action == 'history':
                value = ([asdict(goal) for goal in await service.store.history(identifier)] if identifier else
                         [asdict(goal) for goal in await service.list()])
            elif action == 'plan':
                value = (asdict(await service.plan(params['content'], identifier)) if 'content' in params else
                         await service.read_plan(identifier))
            elif action == 'report':
                value = await service.report(params['report'], identifier)
            elif action == 'task_update':
                if not isinstance(params.get('task'), dict):
                    raise ValueError('task_update requires a task object')
                value = asdict(await service.task(params['task'], identifier))
            else:
                data = {key: item for key, item in params.items() if key not in {'action', 'goal_id'}}
                if 'criteria' in data:
                    data['criteria'] = _strings(data['criteria'])
                if action in {'update', 'activate'} and 'version' not in data:
                    raise ValueError('Update/activate requires the observed Goal version')
                if action == 'activate' and not identifier:
                    raise ValueError('Activate requires the exact current goal_id')
                value = asdict(await service.change(action, identifier, context=context(), **data))
            # Preserve all filters/detail in continuations. Hash the exact selected
            # file facts plus selection, so neither edits nor query switches mix pages.
            selection = {key: item for key, item in params.items() if key not in {'revision', 'offset', 'limit'}}
            revision = await run_owned(snapshot_revision, value, selection)
            if 'revision' in params and params['revision'] != revision:
                raise ValueError('Goal/task snapshot changed; restart reading rather than mix snapshots')
            # Mutations return a compact record. Only read actions can be paged;
            # their hash pins the actual task-file snapshot, not only Goal version.
            rendered = await run_owned(response, lambda: value,
                {'kind': action, 'revision': revision}, {**selection, 'revision': revision},
                page, committed=committed)
            service._check()
            return AgentToolResult.from_text(rendered)
        except _ArgumentError as exc:
            return AgentToolResult.from_text(json_text({'error': str(exc), **exc.data}, ensure_ascii=False),
                                             is_error=True)
        except (ValueError, KeyError, TypeError) as exc:
            return AgentToolResult.from_text(json_text({'error': str(exc)}, ensure_ascii=False), is_error=True)
        # Storage/host failures deliberately propagate to the runtime, not a fake success.

    return AgentTool('goal', 'Manage the current Goal and authoritative task files. The main Agent plans, '
        'assigns using existing tools and decides completion; this tool never runs a worker/reviewer. '
        'status/summary read facts and latest report without the plan; status(detail=true) preserves full status. '
        'tasks reads task_id or status/worker filters; use next_query unchanged for byte pages. '
        'plan reads when content is omitted, writes when supplied. report records the main Agent assessment '
        'with expected_version; no semantic verdict or wait release. '
        'wait(user) requires question/impact/resume_when; saving is not delivery. '
        'update accepts title/outcome/criteria with version and optional goal_id, never reason. '
        'activate requires goal_id/version/reason and releases a satisfied wait; resume only restores a user pause. '
        'Use the goal skill; load it through the host skill mechanism if not already active. '
        'Explicit user authority is required to create/revise a target '
        'or release a user pause.',
        {'type': 'object', 'required': ['action'], 'additionalProperties': False, 'properties': properties},
        execute=execute)
