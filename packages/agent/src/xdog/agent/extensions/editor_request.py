"""Write-only editor proposals: no draft read, agent input, or tool permission."""
from __future__ import annotations

import re
from typing import Any

from xdog.agent.extensions.editor import text

# Render, terminal interception, providers, setup/discovery and transport callbacks
# are intentionally absent. Custom input inherits its original suspended callback.
METHODS = frozenset({'xdog/command', 'tools/call', 'xdog/event', 'xdog/pre', 'xdog/post',
                     'xdog/input', 'xdog/context_finish', 'xdog/custom_frame'})


def proposal(value: Any) -> str:
    selected = text(value)
    if any(ord(char) < 32 and char not in '\n\t' or 127 <= ord(char) < 160 for char in selected):
        raise ValueError('Editor proposal contains unsupported control characters')
    return selected


def validate_request(value: dict[str, Any]) -> None:
    proposal(value.get('text'))
    if any(key in value for key in ('revision', 'cursor', 'image_count', 'images', 'session_id', 'child_id')):
        raise ValueError('Editor proposal cannot select an owner or borrow a composer receipt')


def validate_answer(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'approved', 'revision'}
            or type(value['approved']) is not bool or not isinstance(value['revision'], str)
            or (not re.fullmatch('[a-f0-9]{64}', value['revision']) if value['approved'] else value['revision'] != '')):
        raise ValueError('Editor proposal requires an explicit review and opaque host revision')
    return dict(value)


async def reviewed(extension: str, dialog: dict[str, Any], *,
                   ask: Any, commit: Any, discard: Any = None) -> dict[str, Any] | None:
    """Host adapter: review now, stage once after the original callback succeeds.

    ask(extension, projected_dialog) returns the validated opaque review receipt.
    commit(event) is awaited; discard(event) is synchronous. Both must preserve the
    exact event identity and perform their own host/transport ownership checks.
    """
    import uuid

    from xdog.agent.extensions.invocation import current_invocation
    lease = current_invocation()
    if (lease is None or lease.extension != extension or lease.method not in METHODS
            or dialog.get('kind') != 'editor' or dialog.get('operation') != 'propose'):
        return None
    validate_request(dialog)
    identity = {'id': uuid.uuid4().hex, 'invocation': lease.id, 'session_id': lease.session_id,
                'child_id': lease.child_id, 'method': lease.method}
    event = {'extension': extension, 'editor_write': identity, 'text': proposal(dialog['text'])}
    def abandon() -> None:
        if discard is not None:
            discard({'type': 'extension_editor_write_discard', **event})
    staged = False
    try:
        answer = validate_answer(await ask(extension, {**dialog, 'editor_write': identity}))
        if answer is None or not answer['approved']:
            return answer
        async def apply() -> None:
            if lease.owner_alive():
                await commit({'type': 'extension_editor_write_commit', **event, 'revision': answer['revision']})
            else:
                abandon()
        staged = lease.defer(apply, abandon)
        return answer if staged else None
    finally:
        if not staged:
            abandon()
