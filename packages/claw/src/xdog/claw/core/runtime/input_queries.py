"""Read-only native input recovery; never constructs or wakes another Agent."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from xdog.claw.core.inputs.consumer import InputService
from xdog.claw.core.inputs.models import InputScope, ReplyTarget
from xdog.claw.core.runtime.display_events import display_result


def _label(value: object, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or '\x00' in value or len(value.encode()) > maximum:
        raise ValueError('Invalid native receipt identity')
    return value


class NativeInputReceipts:
    def __init__(self, inputs: InputService, scope_for_group: Callable[[str], InputScope | None]) -> None:
        self.inputs, self.scope_for_group = inputs, scope_for_group

    def _scope(self, group: str) -> InputScope:
        scope = self.scope_for_group(_label(group))
        if scope is None:
            raise ValueError('Unknown native input group')
        return scope

    async def namespace(self, group: str) -> str:
        return await self.inputs.store.namespace(self._scope(group))

    async def read(self, group: str, namespace: str, message_id: str, *, wait: bool = False
                   ) -> dict[str, Any] | None:
        scope = self._scope(group)
        _label(message_id)
        if (not message_id.startswith('native:') or not message_id[len('native:'):].strip()
                or type(wait) is not bool or not isinstance(namespace, str) or len(namespace) != 32
                or any(char not in '0123456789abcdef' for char in namespace)):
            raise ValueError('Invalid native receipt selection')
        if await self.inputs.store.namespace(scope) != namespace:
            raise ValueError('Native inbox namespace changed; no replay or reroute')
        record = await self.inputs.store.get(scope, message_id)
        if record is not None:
            if record.envelope.reply != ReplyTarget('tui', message_id[len('native:'):]):
                raise ValueError('Input identity belongs to another host binding')
            if wait:
                record = await self.inputs.wait(scope, message_id)
        if self._scope(group) != scope or await self.inputs.store.namespace(scope) != namespace:
            raise ValueError('Native input workspace or inbox changed during read')
        if record is None:
            return None
        return {'id': message_id, 'status': record.status, 'attempt_id': record.attempt_id,
                'session_id': record.session_id, 'response': display_result(record.response),
                'error': display_result(record.error[:1024])}
