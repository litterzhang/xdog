"""Mode-neutral controller holder; endpoint/lease composition is added separately."""

from __future__ import annotations

from typing import TYPE_CHECKING

from xdog.coding.core.sessions.controller import SessionController

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession


class SessionRuntime:
    def __init__(self, session: AgentSession, *, bound: bool) -> None:
        self.session = session
        self.controller = SessionController(session, bound=bound)

    def start(self) -> None:
        self.controller.start()
