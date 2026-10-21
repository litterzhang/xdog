"""Owner-bound child admission ports, independent of installed feature schemas."""
from __future__ import annotations

import asyncio
import copy
import inspect
import threading
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from xdog.agent.capabilities.runtime import ParentPort
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.agent.cross_loop import settle_on
from xdog.agent.session_input import SessionInput
from xdog.agent.supervision import ChildBackend, ChildSpec
from xdog.agent.supervision_policy import _matches


class CapabilityHost(Protocol):
    scope: str
    owner_prefix: str
    identity: str
    workspace: Path
    storage_root: Path
    namespace: str
    branch: str
    def valid(self) -> bool: ...
    def accepting(self) -> bool: ...
    def owns_parent(self, parent: ParentPort) -> bool: ...
    async def prepare_parent(self) -> ParentPort: ...
    async def enqueue(self, message: SessionInput) -> str: ...
    async def receipt(self, message: SessionInput) -> str | None: ...
    def subscribe_completed(self, callback: Callable[[], None]) -> Callable[[], None]: ...
    async def child_record(self, session: str, branch: str, identifier: str) -> dict[str, Any] | None: ...
    def subscribe_events(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]: ...
    def execution_snapshot(self) -> dict[str, Any]: ...
    async def latest_execution(self) -> dict[str, Any] | None:
        """Latest durable execution; None means no fact, not unavailable storage.

        IDs match execution.settled, not owner generations. message_ids are the
        authoritative execution inputs. Optional settled_at is Unix seconds only
        when persisted by the execution owner; running has no settled_at.
        Legacy facts lacking metadata return availability=unavailable with reason.
        Retirement before admission returns None; corrupt
        data and storage errors raise. Retirement during owned IO cannot hide its
        actual error. Running is a fact, never authorization to replay execution.
        """
        ...
    async def acp_target(self) -> dict[str, Any] | None: ...


@dataclass
class _Admission:
    owner: ChildPort
    narrow: Callable[[tuple[AgentTool, ...]], tuple[AgentTool, ...]]
    tools: Callable[[ChildSpec], Awaitable[tuple[AgentTool, ...]]]
    admitted: Callable[[ChildSpec], None]
    loop: asyncio.AbstractEventLoop
    claimed: bool = False


_admission: ContextVar[_Admission | None] = ContextVar('capability_child_admission', default=None)


class ChildPort:
    """The original host execution port; copied admission cannot start a second role."""
    def __init__(self, session: str, branch: str, parent_tools: Callable[[], Sequence[AgentTool]],
                 *, parent_valid: Callable[[str], bool]) -> None:
        self.session, self.branch = session, branch
        self.parent_tools = parent_tools
        self._parent_valid = parent_valid
        self.supervisor: ChildBackend
        self._lock = threading.Lock()
        self._roles: dict[str, _Admission] = {}
        self._bound: dict[str, tuple[AgentTool, ...]] = {}
        self._parent_only: dict[int, AgentTool] = {}
        self._loops: dict[str, asyncio.AbstractEventLoop] = {}

    async def start(self, prompt: str, *, access: str, config: dict[str, Any],
                    narrow: Callable[[tuple[AgentTool, ...]], tuple[AgentTool, ...]],
                    tools: Callable[[ChildSpec], Awaitable[tuple[AgentTool, ...]]],
                    admitted: Callable[[ChildSpec], None]) -> str:
        ticket = _Admission(self, narrow, tools, admitted, asyncio.get_running_loop())
        token = _admission.set(ticket)
        try:
            identifier = await self.supervisor.start(prompt, access=access, inherit_context=False, config=config)
            if not ticket.claimed:
                raise ValueError('Capability child did not receive actual admission')
            return identifier
        finally:
            _admission.reset(token)

    def capture(self, spec: ChildSpec, tools: tuple[AgentTool, ...],
                allowed: tuple[str, ...] | None) -> tuple[str, ...] | None:
        # Pin parent-only identities at real admission, before asynchronous startup.
        # Later inventory replacement cannot leak a retired capability to this child.
        parent_only = tuple(self.parent_tools())
        with self._lock:
            self._parent_only.update((id(tool), tool) for tool in parent_only)
        ticket = _admission.get()
        if ticket is None or ticket.owner is not self:
            return allowed
        with self._lock:
            if ticket.claimed:
                return allowed
            narrowed = ticket.narrow(tools)
            if any(not any(tool is original for original in tools) for tool in narrowed):
                raise ValueError('Child policy returned an ungranted tool object')
            ticket.admitted(spec)
            ticket.claimed = True
            self._roles[spec.id] = ticket
            self._loops[spec.id] = ticket.loop
        return tuple(tool.name for tool in narrowed if allowed is None or tool.name in allowed)

    async def tools(self, spec: ChildSpec) -> tuple[AgentTool, ...]:
        with self._lock:
            ticket = self._roles.get(spec.id)
        if ticket is None:
            return ()
        loop = self._loops[spec.id]
        async def originals_on_owner() -> tuple[AgentTool, ...]:
            return await ticket.tools(spec)
        originals = await settle_on(loop, originals_on_owner())
        def bridge(tool: AgentTool) -> AgentTool:
            schema = copy.deepcopy(tool.parameters)
            original_validator = tool.validate_arguments
            bound: AgentTool
            def check(cancel: asyncio.Event | None) -> None:
                if cancel is not None and cancel.is_set():
                    raise asyncio.CancelledError
                if self.closed or not self._parent_valid(spec.id):
                    raise ValueError("Original child capability parent retired before execution")
                state = self.supervisor.capture_source(spec.id)
                if (not state.settling or state.status != 'running'
                        or not self.permits(spec.id, tool.name, (bound,))):
                    raise ValueError("Original child capability admission is no longer running")
            async def execute(call: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                              on_update: Any = None, ctx: Any = None) -> AgentToolResult:
                check(cancel)
                async def invoke() -> AgentToolResult:
                    check(cancel)  # A remote-loop dispatch may have awaited its original owner.
                    validators = (original_validator, tool.validate_arguments)
                    seen: set[int] = set()
                    for validate in validators:
                        if validate is not None and id(validate) not in seen:
                            seen.add(id(validate))
                            value = validate(params)
                            if inspect.isawaitable(value):
                                await value
                            check(cancel)  # Validator IO cannot carry authority across revocation.
                    if not (_matches(schema, params) and _matches(tool.parameters, params)):
                        raise ValueError("Child capability arguments exceed original/current schema")
                    check(cancel)
                    assert tool.execute is not None
                    return await tool.execute(call, params, None, None, ctx)
                return await settle_on(loop, invoke())
            bound = replace(tool, parameters=copy.deepcopy(schema), execute=execute)
            return bound
        result = tuple(bridge(tool) for tool in originals)
        with self._lock:
            self._bound[spec.id] = result
        return result

    def permits(self, identifier: str, name: str, tools: Sequence[AgentTool]) -> bool:
        selected = next((tool for tool in tools if tool.name == name), None)
        with self._lock:
            return selected is not None and any(selected is tool for tool in self._bound.get(identifier, ()))

    def inherited(self, tool: AgentTool) -> bool:
        with self._lock:
            captured = self._parent_only.get(id(tool)) is tool
        return not captured and not any(tool is value for value in self.parent_tools())

    @property
    def active(self) -> bool:
        return bool(self.supervisor.active)

    @property
    def closed(self) -> bool:
        return self.supervisor.closed

    async def inspect(self, identifier: str) -> dict[str, Any]:
        return await self.supervisor.control('inspect', identifier)

    async def cancel(self, identifier: str) -> None:
        await self.supervisor.control('cancel', identifier)

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        return self.supervisor.subscribe(callback)

    def settled(self, identifier: str) -> bool:
        return not self.supervisor.capture_source(identifier).settling
