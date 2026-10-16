"""Coding's canonical next-prompt context persistence seam; no UI or model policy."""
from __future__ import annotations

import threading
from dataclasses import replace
from typing import TYPE_CHECKING

from xdog.agent.context_messages import ContextAdmissionRejected, ContextMessages
from xdog.agent.core import AgentMessage
from xdog.agent.supervision_transfer import settled_image_work

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession


def create(session: AgentSession) -> ContextMessages:
    def scope() -> tuple[str, str]:
        return session.session_id, session.session_data.active_branch
    async def persist(history: tuple[AgentMessage, ...]) -> None:
        data, original = session.session_data, scope()
        from xdog.agent.extensions.context_message import images
        from xdog.ai.utils.image_files import validate_references
        pending_images = images(history[len(session.agent.messages):])
        try:
            await settled_image_work(lambda: validate_references(pending_images, session.model_info))
        except ValueError as exc:
            raise ContextAdmissionRejected(str(exc)) from exc
        staged = replace(data, messages=list(history))
        written = threading.Event()
        def save() -> None:
            if session.session_data is not data or scope() != original:
                raise ValueError('Original Coding context owner changed')
            session.session_manager.save_session(staged)
            written.set()
        try:
            await settled_image_work(save)
        finally:
            if written.is_set() and session.session_data is data and scope() == original:
                data.messages = list(history)
                session.agent.replace_messages(list(history))
    store_owner = ContextMessages(session.session_manager._dir / '.context-messages', scope=scope,
        history=lambda: session.agent.messages, persist=persist,
        publish=lambda event: session.extensions.publish(event))

    # Retain historical next-prompt capture/admission and typed-image persistence.
    # Extension-triggered steer/follow-up/continuation has no remaining request caller.
    return store_owner
