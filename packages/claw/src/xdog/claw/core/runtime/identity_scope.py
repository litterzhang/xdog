"""One original Claw input's model/execution ceiling, not a second runtime."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from xdog.agent.extensions.providers import ExtensionProviderStream
from xdog.ai.types import AssistantMessage, Context, StreamOptions
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.identity import ExecutionPolicy, IdentityResolver, InputIdentity, MessageSource
from xdog.claw.core.identity.permissions import IdentityPermissions
from xdog.claw.core.types import ExternalInput, GroupInput, UserInput

if TYPE_CHECKING:
    from xdog.claw.core.runtime.session import AgentSession


@dataclass(frozen=True)
class InputExecution:
    identity: InputIdentity
    policy: ExecutionPolicy
    input_id: str
    current: Callable[[], bool] = lambda: True


def resolve(value: GroupInput, input_id: str, *, permissions: IdentityPermissions | None = None,
            current: Callable[[], bool] = lambda: True) -> InputExecution:
    identity = value.identity
    if identity is None:
        # Direct session composition is a trusted host API, not a remote endpoint.
        if isinstance(value, ExternalInput):
            identity = IdentityResolver.system(MessageSource('acp'))
        elif isinstance(value, UserInput) and value.metadata.get('input_origin', 'user') == 'user':
            identity = IdentityResolver.local(MessageSource(value.channel or 'local'))
        else:
            identity = IdentityResolver.system(MessageSource('internal'))
    return InputExecution(identity, ExecutionPolicy.for_identity(identity, permissions=permissions), input_id,
                          current=current)


def permission_guard(session: AgentSession) -> Callable[[str], bool]:
    """Capture the original input ceiling, not whichever speaker runs next."""
    scope = session._input_execution
    def allowed(name: str) -> bool:
        if scope is None:
            return True  # Explicit host maintenance, not a received model input.
        current = session._runtime.group.permissions.tools_for(scope.identity)
        return (scope.current() and scope.policy.allows_tool(name)
                and (current is None or name in current))
    return allowed


def allows_tool(session: AgentSession, name: str) -> bool:
    return permission_guard(session)(name)


class SessionStream(ExtensionProviderStream):
    """Last host-owned request boundary; catalogs/providers remain the existing adapter."""
    def __init__(self, session: AgentSession, base: object) -> None:
        from typing import cast

        from xdog.agent.core import StreamFn
        super().__init__(session.extensions, cast(StreamFn, base))
        self.session = session

    def __call__(self, model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
        scope = self.session._input_execution
        if scope is not None:
            if not scope.current():
                raise ValueError("Original input identity grant is no longer current")
            context = replace(context, tools=tuple(tool for tool in context.tools or ()
                                                  if allows_tool(self.session, tool.name)))
        return super().__call__(model, context, options)
