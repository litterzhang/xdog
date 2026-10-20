"""Validate persisted message structure without executing/projecting custom data.

The shared Agent codec deliberately tolerates unknown parts and supplies defaults.
Persistence cannot use that tolerance to silently discard damaged context before
rotation. This boundary accepts its supported message shapes and Claw's historical
system-role compaction summaries; opaque custom payloads remain opaque.
"""
from __future__ import annotations

from typing import Any


def validate_entry(row: dict[str, Any]) -> None:
    role = row.get('role')
    if role == 'custom':
        if not isinstance(row.get('type', 'custom'), str):
            raise ValueError('Invalid custom message type')
        return
    content = row.get('content', [])
    if role == 'system' and row.get('is_compaction') is True and isinstance(content, str):
        return
    if role not in ('user', 'assistant', 'toolResult'):
        raise ValueError('Invalid transcript message role')
    if isinstance(content, str) and role in ('user', 'assistant'):
        return  # Supported historical text format.
    if not isinstance(content, list):
        raise ValueError('Invalid transcript message content')
    allowed = ('text', 'thinking', 'toolCall') if role == 'assistant' else ('text', 'image')
    for part in content:
        if not isinstance(part, dict):
            raise ValueError('Invalid transcript content part')
        kind = part.get('type', 'text')
        if kind not in allowed:
            raise ValueError('Unsupported transcript content part; cannot discard history')
        fields = {'text': ('text',), 'image': ('data', 'mime_type'),
                  'thinking': ('thinking',), 'toolCall': ('id', 'name')}[kind]
        if any(key in part and not isinstance(part[key], str) for key in fields):
            raise ValueError('Invalid transcript content field')
        if kind == 'toolCall' and not isinstance(part.get('arguments', {}), dict):
            raise ValueError('Invalid persisted tool arguments')
        if kind == 'thinking' and type(part.get('redacted', False)) is not bool:
            raise ValueError('Invalid persisted reasoning flag')
    if role == 'toolResult':
        if (any(key in row and not isinstance(row[key], str) for key in ('tool_call_id', 'tool_name'))
                or type(row.get('is_error', False)) is not bool):
            raise ValueError('Invalid persisted tool result')
