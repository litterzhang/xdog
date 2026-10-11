"""Independent RPC provider authentication on the original extension command owner."""
from __future__ import annotations

from typing import Any

from xdog.agent.extensions.provider_oauth_host import ProviderAuthHost


class RpcProviderAuth:
    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.enabled = False
        self.generation = 0
        self.host: ProviderAuthHost | None = None

    def negotiate(self, enabled: bool) -> None:
        self.enabled, self.generation = enabled, self.generation + 1
        if self.host is not None:
            self.host.revoke()
        self.owner.session.extension_dialogs.cancel_where(lambda dialog: bool(
            {'provider_auth_host', 'provider_auth_menu'} & dialog.keys()))

    def bind(self, captured: tuple[str, str, str]) -> ProviderAuthHost:
        owner, session, generation = self.owner, self.owner.session, self.generation
        runtime, agent = session.extensions, session.agent
        def current() -> bool:
            return (self.enabled and self.generation == generation and not owner.closed
                    and owner.scope == captured and owner.control == (captured[1], captured[0])
                    and owner.session is session and session.session_id == captured[0]
                    and session.extensions is runtime and session.agent is agent
                    and not agent.is_streaming and not session.children.supervisor.active
                    and not session._compacting and not session.permissions.has_pending_requests
                    and (owner.task is None or not owner.task.cancelling()))
        self.host = ProviderAuthHost(runtime, owner.handler, current, native_storage=False)
        return self.host

    async def command(self, arguments: str) -> dict[str, Any]:
        host, session = self.host, self.owner.session
        if host is None or session._auth_changing:
            raise ValueError('Provider authentication owner unavailable/busy')
        session._auth_changing = True
        try:
            return await host(arguments)
        finally:
            session._auth_changing = False

    def owns(self, dialog: dict[str, Any]) -> bool:
        return (self.enabled and self.host is not None
                and (self.host.owns(dialog) or self.host.owns_menu(dialog)))

    def finish(self, host: ProviderAuthHost) -> None:
        host.revoke()
        if self.host is host:
            self.host = None
