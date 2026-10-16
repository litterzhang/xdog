"""Compose one durable intake coordinator for an actual lease-owning controller."""
from __future__ import annotations

from typing import TYPE_CHECKING

from xdog.coding.core.sessions.inbox_runtime import InboxCoordinator
from xdog.coding.core.sessions.inbox_store import InboxStore

if TYPE_CHECKING:
    from xdog.coding.core.sessions.controller import SessionController


def attach_inbox(controller: SessionController, store: InboxStore | None = None) -> InboxCoordinator:
    if controller.inbox is not None and not controller.inbox._retired:
        return controller.inbox
    session = controller.session
    selected = store or InboxStore(session.session_manager._dir, session.session_manager._dir.parent / 'inbox')
    coordinator = InboxCoordinator(controller, selected)
    controller.inbox = coordinator
    controller._inputs_ready = False
    return coordinator
