"""Bounded live hook declarations, without callbacks or command/UI authority."""
from __future__ import annotations

from typing import Any

EVENTS = frozenset({'session_start', 'turn_start', 'turn_end', 'message_end', 'tool_start', 'tool_end', 'session_end',
                    'session_before_compact', 'session_compact'})
FLAGS = ('pre', 'post', 'context', 'input')


def declaration(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {'revision', 'events', *FLAGS}
            or type(value['revision']) is not int or not 0 <= value['revision'] <= 2**53):
        raise ValueError('Invalid live extension hook declaration')
    events = value['events']
    if (not isinstance(events, list) or len(events) > len(EVENTS)
            or any(not isinstance(event, str) or event not in EVENTS for event in events)
            or len(set(events)) != len(events) or any(type(value[flag]) is not bool for flag in FLAGS)):
        raise ValueError('Invalid extension hook events/flags')
    return {**value, 'events': list(events)}
