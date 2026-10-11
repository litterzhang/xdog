"""Private idle-message image grants behind the existing native command-success fence."""
from __future__ import annotations

import base64
import copy
from typing import Any

from xdog.agent.extensions.message_request import PreparedMessage, prepare
from xdog.agent.supervision_transfer import settled_image_work
from xdog.ai.utils.image_files import validate_references
from xdog.tui.attachments import Attachment, attachment


class NativeMessageInputs:
    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self._pending: dict[str, tuple[PreparedMessage, Any, Any, str, Any, str, tuple[Attachment, ...]]] = {}

    async def ask(self, extension: str, dialog: dict[str, Any]) -> Any:
        ui, session, command = self.ui, self.ui._session, self.ui._command
        if dialog.get('kind') != 'session' or dialog.get('operation') != 'message':
            return await session.extension_dialogs.ask(extension, dialog)
        context, profile, model = ui._mcp_context_key(), copy.deepcopy(session.model_info), session.model
        def alive() -> bool:
            return (ui._session is session and ui._command is command and command is not None
                    and not command.cancel_requested
                    and command.name.lower().startswith('ext-' + extension.lower() + '-')
                    and ui._mcp_context_key() == context and not ui._worker_active and not ui._exit_requested
                    and not session.agent.is_streaming and not session.children.supervisor.active
                    and not session.permissions.has_pending_requests and session.model == model
                    and session.model_info == profile)
        if (not alive() or ui._extension_navigation is not None or ui._extension_navigation_review is not None
                or len(self._pending) >= 8):
            return False
        selected = await settled_image_work(lambda: prepare(extension, dialog))
        await settled_image_work(lambda: validate_references(selected.images, profile))
        native = await settled_image_work(lambda: tuple(
            attachment(base64.b64decode(image.data, validate=True), f'extension-image-{index}')
            for index, image in enumerate(selected.images)))
        if not alive():
            return False
        key = selected.review['message_review']
        self._pending[key] = selected, session, command, context, profile, model, native
        accepted = False
        try:
            accepted = await session.extension_dialogs.ask(extension, selected.review) is True
            return accepted and alive() and selected.matches(selected.review)
        finally:
            if not accepted or not alive():
                self._pending.pop(key, None)

    def take(self, dialog: dict[str, Any], *, consume: bool = True) -> tuple[PreparedMessage, tuple[Attachment, ...]]:
        key = dialog.get('message_review', '')
        grant = self._pending.get(key)
        if grant is None:
            raise ValueError('Idle message private review grant unavailable')
        selected, session, command, context, profile, model, native = grant
        ui = self.ui
        if (not selected.matches(dialog) or ui._session is not session or ui._command is not command
                or command.cancel_requested or ui._mcp_context_key() != context
                or session.model_info != profile or session.model != model):
            raise ValueError('Idle message owner/model/review changed')
        if consume:
            self._pending.pop(key, None)
        return selected, native

    def clear(self) -> None:
        self._pending.clear()
