"""Owned compaction preparation and bounded extension decisions, not host execution authority."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from xdog.agent.core import AgentMessage
from xdog.ai.types import UserMessage


@dataclass(frozen=True)
class CompactionDecision:
    cancel: bool = False
    reason: str = ''
    summary: str | None = None
    first_kept_index: int | None = None
    details: Any = None
    source: str = 'builtin'


class CompactionVeto(ValueError):
    """Explicit public extension refusal, distinct from private provider failures."""


def copied_details(value: Any) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=True, allow_nan=False)
        if len(encoded.encode()) > 65536:
            raise ValueError('Compaction details exceed 64 KiB')
        return json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError('Compaction details require bounded finite JSON') from exc


def decision(value: Any) -> CompactionDecision:
    if value is None or value == {}:
        return CompactionDecision()
    if not isinstance(value, dict):
        raise ValueError('Invalid compaction decision')
    if value.get('cancel') is True and set(value) <= {'cancel', 'reason'}:
        reason = value.get('reason', '')
        if not isinstance(reason, str) or len(reason.encode()) > 2048:
            raise ValueError('Invalid compaction cancellation reason')
        return CompactionDecision(cancel=True, reason=reason)
    summary = value.get('summary')
    cut = value.get('first_kept_index')
    if (set(value) - {'summary', 'first_kept_index', 'details'} or not isinstance(summary, str)
            or not summary.strip() or len(summary.encode()) > 256 * 1024
            or cut is not None and (type(cut) is not int or cut < 1)):
        raise ValueError('Invalid extension compaction summary/boundary')
    return CompactionDecision(summary=summary, first_kept_index=cut, details=copied_details(value.get('details')))


def wire(value: CompactionDecision) -> dict[str, Any]:
    if value.cancel:
        return {'cancel': True, 'reason': value.reason}
    if value.summary is not None:
        result: dict[str, Any] = {'summary': value.summary, 'details': value.details}
        if value.first_kept_index is not None:
            result['first_kept_index'] = value.first_kept_index
        return result
    return {}


def retained_turn_start(messages: list[AgentMessage], cut: int) -> int:
    """Retain a whole recent user turn, including all subsequent tool exchanges."""
    while cut < len(messages) and not isinstance(messages[cut], UserMessage):
        cut += 1
    if cut == len(messages):
        cut = max((index for index, message in enumerate(messages) if isinstance(message, UserMessage)), default=0)
    return cut


def chosen_boundary(value: CompactionDecision, messages: list[AgentMessage], default: int) -> int:
    cut = default if value.first_kept_index is None else value.first_kept_index
    last_user = max((index for index, message in enumerate(messages) if isinstance(message, UserMessage)), default=0)
    if (type(cut) is not int or not 1 <= cut <= last_user or not isinstance(messages[cut], UserMessage)):
        raise ValueError('Compaction boundary must retain a complete latest user/tool turn')
    return cut


def record(value: CompactionDecision, cut: int, count: int) -> dict[str, Any]:
    result = saved_record({'source': value.source, 'first_kept_index': cut, 'original_count': count,
                           'details': copied_details(value.details)})
    assert result is not None
    return result


def saved_record(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if (not isinstance(value, dict)
            or not {'source', 'first_kept_index', 'original_count', 'details'} <= set(value)
            or set(value) - {'source', 'first_kept_index', 'original_count', 'details', 'recovery_branch_id'}
            or not isinstance(value['source'], str) or not 0 < len(value['source']) <= 64
            or type(value['original_count']) is not int or not 1 < value['original_count'] <= 20000
            or type(value['first_kept_index']) is not int
            or not 1 <= value['first_kept_index'] < value['original_count']):
        raise ValueError('Invalid saved compaction metadata')
    recovery = value.get('recovery_branch_id')
    if recovery is not None and (not isinstance(recovery, str)
                                 or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', recovery) is None):
        raise ValueError('Invalid compaction recovery owner')
    return {**value, 'details': copied_details(value['details'])}
