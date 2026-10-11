"""Opt-in next-user-prompt context proposals; persistence waits for command delivery."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from xdog.agent.supervision import settle_on

if TYPE_CHECKING:
    from xdog.coding.modes.rpc.extensions import RpcExtensions

Commit = Callable[[], Awaitable[None]]


class RpcContextMessages:
    def __init__(self, host: RpcExtensions) -> None:
        self.host = host
        self.enabled = False
        self.delivery_enabled = False
        self.generation = 0
        self._pending: list[Commit] = []
        self._trigger: tuple[Any, Any, str, int] | None = None

    def delivery_modes(self, enabled: bool) -> None:
        if enabled != self.delivery_enabled:
            self.generation += 1
            if self.host.prompt_kind == 'custom_context':
                self.host.abort_prompt()
            self._pending.clear()
            self._trigger = None
        self.delivery_enabled = enabled
        if not enabled:
            self.host.session.extension_dialogs.cancel_where(
                lambda row: row.get('operation') == 'context_message' and row.get('delivery') != 'next_turn')

    def negotiate(self, enabled: bool) -> None:
        if enabled != self.enabled:
            self.generation += 1
            if self.host.prompt_kind == 'custom_context':
                self.host.abort_prompt()
            self._pending.clear()
            self._trigger = None
        self.enabled = enabled
        if not enabled:
            self.host.session.extension_dialogs.cancel_where(lambda row: row.get('operation') == 'context_message')

    async def ask(self, extension: str, dialog: dict[str, Any]) -> bool:
        from xdog.agent.extensions.invocation import current_invocation
        host, captured, generation = self.host, self.host.scope, self.generation
        lease = current_invocation()
        if (captured is None or lease is None or lease.child_id
                or dialog.get('delivery') != 'next_turn' and not self.delivery_enabled):
            return False
        store = getattr(host.session, 'context_messages', None)
        if store is None:
            return False
        command = host.control == (captured[1], captured[0])
        revision = host.navigation.revision if host.navigation is not None else None
        def alive() -> bool:
            return (self.enabled and self.generation == generation and not host.closed
                and host.session.session_id == captured[0]
                and (host.scope == captured or command and host.scope is None
                     and host.control == (captured[1], captured[0]))
                and (host.navigation.revision if host.navigation is not None else None) == revision
                and not host.session.permissions.has_pending_requests)
        async def review(name: str, projected: dict[str, Any]) -> Any:
            if host.scope != captured or not alive():
                return False
            answer = await host.session.extension_dialogs.ask(name, projected)
            return answer if alive() else False
        async def schedule(commit: Commit) -> None:
            async def queue() -> None:
                if not alive():
                    return
                if command:
                    if len(self._pending) >= 8:
                        raise ValueError('Command context proposal capacity exceeded')
                    self._pending.append(commit)
                else:
                    await commit()
            await settle_on(host.loop, queue())
        async def trigger(value: dict[str, Any]) -> None:
            if not command or not alive():
                raise ValueError('Idle continuation lost its original delivered command')
            self._trigger = captured, revision, value['id'], generation
        return bool(await store.review(extension, dialog, ask=review, alive=alive, schedule=schedule,
                                       trigger=trigger if command else None))

    async def finish_command(self, successful: bool) -> None:
        pending, self._pending = self._pending, []
        if successful:
            for commit in pending:
                try:
                    await commit()
                except BaseException:
                    self.host.emit({'type': 'extension_context_delivery_result', 'success': False,
                                    'error': 'Canonical delivery failed/uncertain; not replayed'})
                    raise

    def finish_trigger(self, successful: bool) -> None:
        value, self._trigger = self._trigger, None
        if value is None:
            return
        if not successful or self.host.prompting or self.host.scope is not None:
            if not self.host.closed:
                self.host.emit({'type': 'extension_context_continuation_discarded', 'request_id': value[0][1],
                    'session_id': value[0][0], 'id': value[2],
                    'reason': 'Callback or another reviewed action owns host'})
            return
        host = self.host
        captured, revision, identity, generation = value
        from xdog.coding.modes.rpc.rpc_mode import _handle_prompt
        async def run() -> None:
            history = host.session.agent.messages
            if (host.prompt_task is not asyncio.current_task() or host.closed or not self.enabled
                    or self.generation != generation
                    or host.session.session_id != captured[0] or host.session.agent.is_streaming
                    or host.navigation is None or host.navigation.revision != revision
                    or not history or getattr(history[-1], 'data', {}).get('id') != identity
                    or host.session.permissions.has_pending_requests or host.session.children.supervisor.active):
                return
            async with host.bind('custom-context-' + identity):
                await _handle_prompt(host.session, '', host.emit, input_origin='custom_context')
        host.emit({'type': 'extension_context_continuation', 'request_id': captured[1],
                   'session_id': captured[0], 'id': identity})
        import asyncio
        host.prompt_kind = 'custom_context'
        host._message_prompt_has_images = False
        host.prompt_task = asyncio.create_task(run())
        host.prompt_task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
