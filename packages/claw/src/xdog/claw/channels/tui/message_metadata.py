"""Compact, terminal-safe message annotations, without business/runtime policy."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from xdog.tui.utils import sanitize_terminal_text

_STATUS = {'pending': '待发送', 'sending': '发送中', 'delivered': '已送达', 'unknown': '未确认',
           'cancelled': '已取消', 'disabled': '回复关闭', 'not_recorded': '无投递记录', 'local': '本地显示'}


def clean_metadata(raw: object) -> dict[str, Any]:
    """Allow only bounded display labels; never show recipient tokens or raw contexts."""
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in ('role', 'principal', 'source', 'sender', 'account', 'group', 'input_id', 'session_id', 'endpoint'):
        value = raw.get(key)
        if isinstance(value, str):
            result[key] = sanitize_terminal_text(value)[:256].replace('\n', ' ').replace('\r', ' ')
    routes = raw.get('destinations')
    if isinstance(routes, (list, tuple)):
        result['destinations'] = []
        for route in routes[:16]:
            if isinstance(route, Mapping) and isinstance(route.get('channel'), str):
                result['destinations'].append({'channel': sanitize_terminal_text(route['channel'])[:128]
                    .replace('\n', ' ').replace('\r', ' '),
                    'status': route.get('status') if isinstance(route.get('status'), str)
                    and route['status'] in _STATUS else 'not_recorded'})
                identifier = route.get('output_id')
                if isinstance(identifier, str):
                    result['destinations'][-1]['output_id'] = sanitize_terminal_text(identifier)[:256]

    handoff = raw.get('handoff')
    if isinstance(handoff, Mapping):
        result['handoff'] = {key: sanitize_terminal_text(value)[:256] if isinstance(value, str) else value
            for key in ('source_session_id', 'target_session_id', 'source_digest', 'source_message_count',
                        'reason', 'created_at', 'status', 'memory_status')
            if isinstance((value := handoff.get(key)), str) or type(value) is int}
    return result


def details(metadata: object, *, tool_call_id: str = '') -> str:
    """Explicit whitelist, not raw host context/cookies. No display label grants authority."""
    value = clean_metadata(metadata)
    if tool_call_id:
        value['tool_call_id'] = sanitize_terminal_text(tool_call_id)
    return '\n\nMessage details:\n' + json.dumps(value, ensure_ascii=False, indent=2) if value else ''


def label(metadata: dict[str, Any], kind: str) -> str:
    source = metadata.get('source') or 'unknown'
    role = metadata.get('role') or 'unknown'
    speaker = metadata.get('sender') or metadata.get('principal') or ''
    if len(speaker) > 18:
        speaker = speaker[:8] + '…' + speaker[-4:]
    actor = role + (':' + speaker if speaker and role in {'user', 'external_user'} else '')
    if kind == 'user':
        return actor + ' · ' + source
    parts = [kind, source]
    if kind in {'agent', 'tool'}:
        routes = metadata.get('destinations')
        if routes is None:
            parts[-1] += ' → 未知'
        elif not routes:
            parts[-1] += ' → 无自动回复'
        else:
            unique = dict.fromkeys((r['channel'], r['status']) for r in routes)
            parts[-1] += ' → ' + ', '.join(f'{channel} [{_STATUS[status]}]' for channel, status in unique)
    if kind != 'system' and role != 'unknown':
        parts.append(actor)
    return ' · '.join(parts)
