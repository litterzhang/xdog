"""Live session capability binding without knowing any observer source types."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from xdog.agent import AgentContext, BeforeToolCallContext
from xdog.ai.types import AssistantMessage, ToolCall
from xdog.coding.core.external_events.host import ExternalEventHost
from xdog.coding.core.external_events.inbox import ExternalEventInbox

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession


class ExternalEventBinding:
    def __init__(self, session: AgentSession, *, notify: Callable[[], None] | None = None) -> None:
        self.session = session
        self.inbox = ExternalEventInbox()
        self._scope = self.scope()
        self._closed = False
        self.host = ExternalEventHost(self.inbox, scope=self.scope, approve=self._approve, notify=notify)

    def scope(self) -> tuple[str, str]:
        return self.session.session_id, self.session.session_data.active_branch

    def _environment(self) -> None:
        context = self.session.agent.tool_context
        existing = context.get("subprocess_env", {})
        if not isinstance(existing, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                  for k, v in existing.items()):
            raise ValueError("Invalid caller subprocess environment")
        context["subprocess_env"] = {**existing, "XDOG_EVENT_CONTEXT": str(self.host.descriptor)}
        self.session.agent.set_tool_context(context)

    def start(self) -> None:
        try:
            self.host.start()
            self._environment()
        except BaseException:
            self.host.close()
            raise

    def refresh_owner(self) -> None:
        if not self._closed and self.scope() != self._scope:
            self.host.rotate()
            self._scope = self.scope()
            self._environment()

    async def _approve(self, registration: str) -> bool:
        captured = self.scope()
        args = {"registration_id": registration, "session_id": captured[0],
                "branch_id": captured[1], "generation": self.host.generation}
        context = BeforeToolCallContext(
            assistant_message=AssistantMessage(),
            tool_call=ToolCall(id="external-event-resume", name="external_event_resume", arguments=args),
            args=args, context=AgentContext(),
        )
        result = await self.session.permissions.before_tool_call(context)
        return result is None and not self._closed and self.scope() == captured

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.host.close()
