"""Data-only lifecycle provenance. Labels describe admitted operations, never grant authority."""
from __future__ import annotations

from typing import Any

from .host import OperationContext
from .models import Goal

ACTIONS = frozenset({'create', 'pause', 'activate', 'resume', 'wait', 'complete', 'clear'})
_TARGET = {'create': 'active', 'pause': 'paused', 'resume': 'active', 'activate': 'active', 'wait': 'waiting',
           'complete': 'completed', 'clear': 'cleared'}


def transition_data(action: str, context: OperationContext, before: Goal | None, after: Goal) -> dict[str, Any]:
    """Build from the actual transaction's revisions and trusted invocation context."""
    if action not in ACTIONS or not isinstance(context, OperationContext):
        raise ValueError('Invalid Goal transition provenance')
    if ((before is None) != (action == 'create') or after.status != _TARGET[action]
            or before is not None and (before.id != after.id or before.version + 1 != after.version)):
        raise ValueError('Goal transition does not match actual revisions')
    if context.origin == 'native_command' and not context.user_authorized:
        raise ValueError('A native Goal change requires an actual user operation')
    actor = {'native_command': 'user', 'model_tool': 'agent', 'runtime': 'host'}.get(
        context.origin, 'user' if context.user_authorized else 'unknown')
    return {'format': 'xdog.goal_transition.v1', 'goal_id': after.id, 'version': after.version,
            'action': action, 'origin': context.origin, 'actor': actor, 'user_authorized': context.user_authorized,
            'command': '/goal ' + action if context.origin == 'native_command' else '',
            'from': None if before is None else {'status': before.status, 'wait_for': before.wait_for},
            'to': {'status': after.status, 'wait_for': after.wait_for}}
