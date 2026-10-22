"""Explicit proactive messages for the current group's host-bound channels."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from xdog.agent.core import AgentTool, AgentToolResult


def create_message_tool() -> AgentTool:
    async def execute(call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                      on_update: Any = None, ctx: dict[str, Any] | None = None) -> AgentToolResult:
        try:
            if cancel is not None and cancel.is_set():
                raise asyncio.CancelledError
            scope = (ctx or {}).get('_message_port')
            if scope is None:
                raise ValueError('Message tool requires an actual admitted host input scope')
            action = params.get('action')
            if action == 'channels' and set(params) == {'action'}:
                value = await scope.channels()
            elif action == 'send':
                if not {'channel', 'text'} <= params.keys() or set(params) - {'action', 'channel', 'text', 'reference'}:
                    raise ValueError('message.send requires channel and text (optional reference); '
                                     'group/recipient/multicast are not allowed')
                if (not isinstance(params['channel'], str) or not params['channel'].strip()
                        or len(params['channel'].encode()) > 256 or not isinstance(params['text'], str)):
                    raise ValueError('message.send channel and text must be strings')
                reference = params.get('reference')
                if reference is not None and (not isinstance(reference, str) or not reference.strip()
                                              or '\x00' in reference or len(reference.encode()) > 256):
                    raise ValueError('message.send reference must be bounded nonempty text')
                value = await scope.send(call_id, params['channel'], params['text'], cancel, reference=reference)
            else:
                raise ValueError('Use message.channels with no fields, or message.send with channel and text')
            return AgentToolResult.from_text(json.dumps(value, ensure_ascii=False))
        except ValueError as exc:
            return AgentToolResult.from_text(str(exc), is_error=True)
        # Host/storage failures are not falsely reported as a delivered message.

    return AgentTool('message',
        'Proactively send one message to one currently bound channel of this group. '
        'Use channels to list host-issued channel IDs, then send(channel,text). '
        'No arbitrary group/recipient or multicast. Sending is one tool side effect; '
        'it does not change the normal reply route or end this turn. Inspect delivery status; '
        'do not repeat sent text as another ordinary reply or retry an unknown delivery.',
        {'type': 'object', 'additionalProperties': False, 'required': ['action'], 'properties': {
            'action': {'type': 'string', 'enum': ['channels', 'send']},
            'channel': {'type': 'string', 'description': 'send: exact ID returned by channels'},
            'text': {'type': 'string', 'description': 'send: user-visible message text'},
            'reference': {'type': 'string',
                          'description': 'send: optional correlation ID; grants no routing authority'},
        }}, execute=execute)
