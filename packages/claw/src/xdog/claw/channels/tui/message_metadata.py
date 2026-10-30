"""Compact, terminal-safe message annotations, without business/runtime policy."""
from __future__ import annotations

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
    for key in ('role', 'principal', 'source', 'sender', 'account', 'group', 'input_id'):
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
    return result


def label(metadata: dict[str, Any], kind: str) -> str:
    source = metadata.get('source') or 'unknown'
    role = metadata.get('role') or 'unknown'
    speaker = metadata.get('sender') or metadata.get('principal') or ''
    parts = [role if kind == 'user' else kind]
    if kind == 'user' and speaker:
        parts.append('身份:' + speaker)
    parts.append('来源:' + source)
    if kind != 'user':
        parts.append('触发:' + role)
    if metadata.get('group'):
        parts.append('group:' + metadata['group'])
    if kind == 'agent' or kind == 'tool':
        routes = metadata.get('destinations')
        if routes is None:
            parts.append('→ 未知（无路由记录）')
        elif not routes:
            parts.append('→ 无自动回复渠道')
        else:
            unique = dict.fromkeys((r['channel'], r['status']) for r in routes)
            parts.append('→ ' + ', '.join(f'{channel} [{_STATUS[status]}]' for channel, status in unique))
    return ' · '.join(parts)
