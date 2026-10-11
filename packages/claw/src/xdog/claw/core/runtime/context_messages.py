"""Claw original-session/branch canonical custom-context persistence adapter."""
from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from xdog.agent.context_messages import ContextAdmissionRejected, ContextMessages
from xdog.agent.core import AgentMessage
from xdog.agent.supervision_transfer import settled_image_work
from xdog.claw.core.persistence.transcript_convert import live_transcript_snapshot

if TYPE_CHECKING:
    from xdog.claw.core.runtime.session import AgentSession


def create(session: AgentSession) -> ContextMessages:
    store = session._store
    def scope() -> tuple[str, str]:
        return session.meta.session_id, store.active_branch(session.meta.session_id)
    async def persist(history: tuple[AgentMessage, ...]) -> None:
        owner = scope()
        from xdog.agent.extensions.context_message import images
        pending_images = images(history[len(session.agent.messages):])
        try:
            await settled_image_work(lambda: session._runtime.validate_images(pending_images))
        except ValueError as exc:
            raise ContextAdmissionRejected(str(exc)) from exc
        written = threading.Event()
        def save() -> None:
            if scope() != owner or session._runtime._active_session is not session:
                raise ValueError('Original Claw context owner changed')
            rows = live_transcript_snapshot(history, store.load_transcript(owner[0]))
            store.replace_transcript(owner[0], rows)
            written.set()
        try:
            await settled_image_work(save)
        finally:
            if written.is_set() and scope() == owner and session._runtime._active_session is session:
                previous = len(session.agent.messages)
                added = len(history) - previous
                # run_turn reserves the upcoming user's source slot before Agent.prompt.
                # Keep that channel on the user, never on the inserted custom record.
                session._source_channels = {(index + added if index >= previous else index): channel
                                            for index, channel in session._source_channels.items()}
                session.agent.replace_messages(list(history))
                session._persisted_count = len(history)
                session._runtime._source_revision += 1
    store_owner = ContextMessages(store._sessions_dir / '.context-messages', scope=scope,
        history=lambda: session.agent.messages, persist=persist,
        eligible=lambda: session._runtime._active_session is session,
        publish=lambda event: session.extensions.publish(event))

    from xdog.agent.core import CustomAgentMessage
    from xdog.agent.custom_delivery import bind
    async def validate(message: CustomAgentMessage) -> None:
        from xdog.agent.extensions.context_message import images
        if not session.agent.is_streaming:
            from xdog.agent.supervision import settle_on
            async def restore() -> None:
                session._restore_messages()
            if session._host_loop is not None:
                await settle_on(session._host_loop, restore())
            else:
                session._restore_messages()
        await settled_image_work(lambda: session._runtime.validate_images(images((message,))))
    bind(store_owner, session.agent, validate)
    return store_owner


def handler(session: AgentSession, alive: Callable[[], bool], *, schedule: Any = None,
            trigger: Any = None) -> Callable[[str, dict[str, Any]], Awaitable[bool]]:
    """Original native command/run view; never delegate its queue to a child."""
    runtime, extensions = session._runtime, session.extensions
    scope = session.context_messages.scope()
    def current() -> bool:
        return (alive() and runtime._active_session is session and session.extensions is extensions
                and session.context_messages.scope() == scope)
    async def review(extension: str, dialog: dict[str, Any]) -> bool:
        return await session.context_messages.review(extension, dialog,
            ask=session.extension_dialogs.ask, alive=current, schedule=schedule, trigger=trigger)
    return review
