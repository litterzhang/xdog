"""Native Coding binding for the existing owned /extensions command and dialog stack."""
from __future__ import annotations

from typing import Any

from xdog.agent.extensions.provider_oauth_host import ProviderAuthHost
from xdog.tui.components.extension_provider_auth import ExtensionProviderAuth


class NativeProviderAuth:
    def __init__(self, ui: Any) -> None:
        self.ui, self.session = ui, ui._session
        self.command: Any = None
        self.context: Any = None
        self.host = ProviderAuthHost(self.session.extensions, self.session.extension_dialogs.ask,
                                     self.alive, native_storage=True)

    def alive(self) -> bool:
        ui, session = self.ui, self.session
        return bool(self.command is not None and ui._command is self.command and not self.command.cancel_requested
            and self.command.name == 'extensions' and ui._session is session
            and session.extensions is self.host.runtime and not ui._exit_requested
            and self.context == ui._mcp_context_key() and not ui._worker_active
            and not session.agent.is_streaming and not session.children.supervisor.active
            and not session._compacting and ui._permission_prompt is None and not ui._permission_waiters
            and not session.permissions.has_pending_requests and not ui._tui.has_overlay())

    async def __call__(self, arguments: str) -> dict[str, Any]:
        if self.session._auth_changing:
            raise ValueError('Authorization operation already owns this session')
        self.command, self.context = self.ui._command, self.ui._mcp_context_key()
        if not self.alive():
            raise ValueError('Provider authentication requires an original idle native command')
        self.session._auth_changing = True
        try:
            return await self.host(arguments)
        finally:
            self.host.revoke()
            self.session._auth_changing = False
            self.command = None

    def owns(self, dialog: dict[str, Any]) -> bool:
        return self.host.owns(dialog)

    def open_url(self, url: str) -> None:
        panel = self.ui._extension_dialog_panel
        def current() -> bool:
            return (self.alive() and isinstance(panel, ExtensionProviderAuth)
                    and self.ui._extension_dialog_panel is panel and self.ui._tui._focused is panel
                    and panel.focused and not panel._resolved and panel._review._visible
                    and self.host.owns(panel.dialog) and panel.dialog['provider_auth'].get('url') == url)
        if current():
            try:
                self.ui._reviewed_url.start(url, current)  # Distinct fully painted native gesture only.
            except (ValueError, RuntimeError):
                self.ui._chat_log.add_system('Provider URL opening unavailable/busy; no automatic retry')
