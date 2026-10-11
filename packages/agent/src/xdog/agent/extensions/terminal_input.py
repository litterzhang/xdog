"""Bounded terminal interception data; never a terminal writer or permission decision."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TerminalDecision:
    consume: bool = False
    data: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {'consume': self.consume}
        if self.data is not None:
            value['data'] = self.data
        return value


def text(value: Any) -> str:
    if not isinstance(value, str) or len(value.encode('utf-8')) > 16000:
        raise ValueError('Terminal input exceeds its UTF-8 retention limit')
    return value


def metadata(value: Any) -> dict[str, str]:
    if (not isinstance(value, dict) or set(value) != {'kind', 'surface'}
            or value['kind'] not in ('key', 'paste', 'release') or not isinstance(value['surface'], str)
            or not value['surface'] or len(value['surface'].encode()) > 128):
        raise ValueError('Invalid terminal input metadata')
    return {'kind': value['kind'], 'surface': value['surface']}


def decision(value: Any) -> TerminalDecision:
    if value is None:
        return TerminalDecision()
    if (not isinstance(value, dict) or set(value) - {'consume', 'data'}
            or type(value.get('consume', False)) is not bool):
        raise ValueError('Invalid terminal input decision')
    data = text(value['data']) if 'data' in value else None
    return TerminalDecision(value.get('consume', False), data)


def event_kinds(value: Any) -> tuple[str, ...]:
    """Exact bounded event opt-in; omission is handled by the caller, never inferred."""
    if (not isinstance(value, (list, tuple)) or len(value) > 3
            or any(not isinstance(item, str) or item not in ('key', 'paste', 'release') for item in value)
            or len(set(value)) != len(value)):
        raise ValueError('Invalid terminal input event kinds')
    return tuple(kind for kind in ('key', 'paste', 'release') if kind in value)


def event_decision(value: Any, kind: str) -> TerminalDecision:
    result = decision(value)
    if kind == 'release' and result.data is not None:
        raise ValueError('Terminal release observation cannot transform input')
    return result
