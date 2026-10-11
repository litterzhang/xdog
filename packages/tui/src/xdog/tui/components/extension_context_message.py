"""Whole, false-default custom-context review; no model or user-input admission."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from xdog.tui.components.extension_form import ExtensionForm
from xdog.tui.components.extension_navigation import ExtensionNavigation
from xdog.tui.components.mcp_sampling_panel import _json


class ExtensionContextMessage(ExtensionNavigation):
    def __init__(self, extension: str, dialog: dict[str, Any], answer: Callable[[Any], None], current: str) -> None:
        value = json.loads(_json(dialog))
        if (value.get('kind') != 'session' or value.get('operation') != 'context_message'
                or value.get('delivery') not in ('next_turn', 'steer', 'follow_up')
                or type(value.get('display')) is not bool
                or not isinstance(value.get('custom_type'), str) or not isinstance(value.get('content'), list)
                or any(not isinstance(part, dict) or set(part) != {'type', 'text'}
                       or part.get('type') != 'text' or not isinstance(part.get('text'), str)
                       for part in value['content']) or not isinstance(value.get('image_summaries'), list)):
            raise ValueError('Custom context requires the binary-free original-owner review')
        self.dialog = value
        self.form = ExtensionForm(extension, {
            'title': value['title'], 'message': 'Original session: ' + current + '\n' + value.get('message', ''),
            'fields': [{'name': 'approve', 'kind': 'boolean',
                'label': ('Retain this exact next-turn context' if value['delivery'] == 'next_turn'
                          else 'Deliver this canonical custom context'),
                'required': True, 'default': False,
                'description': json.dumps({key: value.get(key) for key in
                    ('custom_type', 'content', 'display', 'details', 'delivery', 'trigger_turn', 'image_summaries')},
                    ensure_ascii=False, indent=2) + '\nAfter this callback succeeds, persist only to this original '
                    'session/branch. next_turn waits for an explicit user; '
                    'steer/follow_up use existing active checkpoints. '
                    'Idle trigger=true starts canonical continuation, not a fabricated user prompt. '
                    'Details remain opaque metadata, not model instructions. No user impersonation, child sharing, '
                    'image opening, draft borrowing, or tool permission.'}],
        }, lambda value: answer(bool(value and value.get('approve') is True)))
