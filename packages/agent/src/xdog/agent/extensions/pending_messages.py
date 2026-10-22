"""Live boolean inspection only; no input bodies, identifiers, ordering or mutation."""
from __future__ import annotations

from typing import Any

METHODS = frozenset({'xdog/command', 'xdog/event', 'xdog/pre', 'xdog/post',
                     'xdog/input', 'xdog/context', 'xdog/context_finish'})
_FIELDS = {'id', 'kind', 'operation', 'title', 'message', 'timeout', 'extension', 'parent_id'}


def request(value: dict[str, Any]) -> None:
    if value.keys() - _FIELDS:
        raise ValueError('Pending-message inspection cannot select an owner or request input contents')
