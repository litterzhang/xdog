"""Independently negotiated write-only draft proposals; clients retain their own CAS."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from xdog.agent.extensions.editor_request import reviewed
from xdog.agent.supervision import settle_on

if TYPE_CHECKING:
    from xdog.coding.modes.rpc.extensions import RpcExtensions


class RpcEditorWrites:
    def __init__(self, host: RpcExtensions) -> None:
        self.host = host
        self.enabled = False
        self.generation = 0
        self._pending: list[tuple[dict[str, Any], Any]] = []

    def negotiate(self, enabled: bool) -> None:
        if enabled != self.enabled:
            self.generation += 1
            self._pending.clear()
        self.enabled = enabled
        if not enabled:
            self.host.session.extension_dialogs.cancel_where(lambda dialog: dialog.get('operation') == 'propose')

    async def ask(self, extension: str, dialog: dict[str, Any]) -> Any:
        host, captured, generation = self.host, self.host.scope, self.generation
        if captured is None or not self.enabled:
            return None
        navigation_revision = host.navigation.revision if host.navigation is not None else None
        command = host.control == (captured[1], captured[0])
        def alive(*, staged: bool = False) -> bool:
            return (not host.closed and self.enabled and self.generation == generation
                and host.session.session_id == captured[0]
                and (host.navigation.revision if host.navigation is not None else None) == navigation_revision
                and (host.scope == captured or staged and command and host.scope is None
                     and host.control == (captured[1], captured[0]))
                and not host.session.permissions.has_pending_requests
                and not any(manager.has_pending_requests for manager in host.session.children.permissions.values()))
        async def review(name: str, projected: dict[str, Any]) -> Any:
            if projected['editor_write']['child_id'] or not alive():
                return None
            answer = await host.session.extension_dialogs.ask(name,
                {**projected, 'editor_write_generation': generation})
            return answer if alive() else None
        async def send(event: dict[str, Any]) -> None:
            if not alive(staged=command):
                return
            payload = {**event, 'type': 'extension_editor_write_apply', 'request_id': captured[1],
                'session_id': captured[0], 'scope': captured[2], 'editor_write_generation': generation,
                'notice': 'Apply request only: client must recheck original reviewed editor owner and one-use CAS'}
            if host.send is not None:
                await host.send(payload)
            else:
                host.emit(payload)
        async def commit(event: dict[str, Any]) -> None:
            async def queue() -> None:
                if alive():
                    if command:
                        if len(self._pending) < 8:
                            self._pending.append((event, send))
                    else:
                        await send(event)
            await settle_on(host.loop, queue())
        return await reviewed(extension, dialog, ask=review, commit=commit)

    async def finish_command(self, successful: bool) -> None:
        pending, self._pending = self._pending, []
        if successful:
            for event, send in pending:
                await send(event)
