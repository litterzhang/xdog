"""Stdlib tool descriptor; service and host authority are closure-bound."""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .manager import ManagedObservers


class EyesTool:
    name = 'eyes'
    description = ('Manage current-group Eyes observers; timing uses native scheduler sources. '
                   'Intake, execution and channel delivery are distinct outcomes.')
    parameters: dict[str, Any] = {
        'type': 'object', 'required': ['action'], 'additionalProperties': False,
        'properties': {
            'action': {'type': 'string', 'enum': ['create', 'list', 'status', 'cancel', 'cancel_notifications']},
            'name': {'type': 'string', 'description': 'Observer name (create)'},
            'source': {'type': 'object', 'description': 'Eyes file or scheduler source (create)'},
            'instruction': {'type': 'string', 'description': 'What the group agent should do and report (create)'},
            'observer_id': {'type': 'string', 'description': 'Observer ID in the current group'},
        },
    }

    def __init__(self, service: ManagedObservers) -> None:
        self._service = service

    async def execute(self, tool_call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                      on_update: Any = None, ctx: dict[str, Any] | None = None) -> str:
        return await self._execute(params, cancel, ctx)

    async def _execute(self, params: dict[str, Any], cancel: asyncio.Event | None,
                       ctx: dict[str, Any] | None) -> str:
        value: object
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        if not isinstance(params, dict):
            raise ValueError('Expected Eyes action parameters')
        action = params.get('action')
        fields = {'create': {'name', 'source', 'instruction'}, 'list': set(), 'status': {'observer_id'},
                  'cancel': {'observer_id'}, 'cancel_notifications': {'observer_id'}}
        if not isinstance(action, str) or action not in fields or set(params) != {'action'} | fields[action]:
            raise ValueError('Expected an Eyes action and its exact required parameters')
        scope = self._service.host.tool_scope(ctx or {})
        group = scope['group_id']
        if action == 'create':
            value = await self._service.create(group, params['name'], params['source'], params['instruction'],
                                               reply=self._service.host.capture_reply(ctx or {}))
        elif action == 'list':
            value = await self._service.list(group)
        else:
            identifier = params['observer_id']
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError('Expected an observer ID')
            value = await getattr(self._service, action)(group, identifier)
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(text.encode()) > 2 * 1024 * 1024:
            raise ValueError('Observer inspection exceeds the bounded tool response; inspect one observer at a time')
        return text
