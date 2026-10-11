"""Bounded command-owned active-tool selection vocabulary, not execution permission."""
from __future__ import annotations

from typing import Any

from xdog.agent.agent import Agent


def names(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None  # Explicit reset to configured defaults.
    if (not isinstance(value, list) or len(value) > 2048
            or any(not isinstance(name, str) or not name or len(name.encode()) > 256 for name in value)
            or len(set(value)) != len(value)):
        raise ValueError('Active tools require bounded unique names or explicit default reset')
    return tuple(value)


def request(value: dict[str, Any]) -> None:
    if 'after_run' in value:
        from xdog.agent.extensions.active_tools_request import request as after_run_request
        after_run_request(value)
    if any(key in value for key in ('target', 'session_id', 'branch_id', 'scope', 'index')):
        raise ValueError('Tool selection cannot change session scope')
    if value['operation'] == 'select_tools':
        if 'names' not in value:
            raise ValueError('Explicit tool selection required')
        names(value['names'])
        if type(value.get('revision')) is not int or value['revision'] < 0:
            raise ValueError('Original tool selection revision required')


def snapshot(agent: Agent, session_id: str) -> dict[str, Any]:
    revision = agent.tool_selection_revision
    value = {'session_id': session_id, 'revision': revision,
             'configured': [tool.name for tool in agent.state.tools],
             'active': [tool.name for tool in agent.active_tools]}
    checked = answer(value)
    if agent.tool_selection_revision != revision:
        raise ValueError('Tool selection changed during inspection')
    return checked


def answer(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {'session_id', 'revision', 'configured', 'active'}
            or not isinstance(value['session_id'], str) or not 0 < len(value['session_id'].encode()) <= 256
            or type(value['revision']) is not int or value['revision'] < 0):
        raise ValueError('Invalid active tool snapshot')
    configured, selected = names(value['configured']), names(value['active'])
    if configured is None or selected is None or not set(selected) <= set(configured):
        raise ValueError('Active tool snapshot exceeds configured inventory')
    return {**value, 'configured': list(configured), 'active': list(selected)}
