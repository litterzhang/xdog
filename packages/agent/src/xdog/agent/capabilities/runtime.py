"""Inert installed-provider discovery and owned capability lifecycle."""
from __future__ import annotations

import asyncio
import copy
import inspect
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any
from weakref import WeakValueDictionary

from xdog.agent.agent import Agent
from xdog.agent.async_work import settle_cleanup, wait_owned
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.agent.tool_effects import ToolEffect

if TYPE_CHECKING:
    from xdog.agent.capabilities.children import ChildPort
    from xdog.agent.children import ChildSpec
    from xdog.agent.extensions.runtime import ExtensionRuntime

_SCOPES = frozenset({"session", "group", "application"})


@dataclass(frozen=True)
class ApplicationPort:
    """Inert application identity, independent of any conversation or tool owner.

    Hosts retain this marker for their application lifetime. It grants no Agent,
    filesystem, permission or routing authority and starts no resources.
    """
    scope: str = field(default='application', init=False)
    identity: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True)
class ParentPort:
    """A pinned public runtime handle, never a raw application/session object."""

    agent: Agent
    extensions: ExtensionRuntime
    children: ChildPort
    valid: Callable[[], bool]
    user_authorized: Callable[[], bool]


def _names(value: Any, *, scopes: bool = False) -> tuple[str, ...]:
    if (not isinstance(value, (tuple, list, frozenset))
            or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", item)
                   for item in value) or len(value) != len(set(value))
            or scopes and not set(value) <= _SCOPES):
        raise ValueError("Invalid installed capability metadata")
    return tuple(value)


def discover(scope: str) -> tuple[Any, ...]:
    """Only trusted installed entry points, never modules named by saved data."""
    if scope not in _SCOPES:
        raise ValueError("Unknown capability host scope")
    providers = []
    names: set[str] = set()
    tools: set[str] = set()
    for entry in sorted(entry_points(group="xdog.capabilities"), key=lambda value: value.name):
        provider = entry.load()
        supported = _names(provider.scopes, scopes=True)
        if scope not in supported:
            continue
        if (not isinstance(provider.name, str) or provider.name != entry.name or provider.name in names
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", provider.name)
                or not callable(getattr(provider, "open", None))):
            raise ValueError("Invalid or duplicate installed capability identity")
        selected = _names(provider.tool_names)
        _names(provider.requirements)
        _names(getattr(provider, "command_names", ()))
        _names(getattr(provider, "optional_requirements", ()))
        _names(getattr(provider, "default_skills", ()))
        _names(getattr(provider, "skill_names", ()))
        if tools.intersection(selected):
            raise ValueError("Duplicate installed capability tool name")
        names.add(provider.name)
        tools.update(selected)
        providers.append(provider)
    return tuple(providers)


class CapabilitySet:
    """One host owner; feature code owns its own tools, data and business lifetime."""

    def __init__(self, scope: str) -> None:
        self.providers = discover(scope)
        self._bindings: dict[str, Any] = {}
        self._tools: dict[int, tuple[Any, AgentTool, Any]] = {}
        self._owned: WeakValueDictionary[int, AgentTool] = WeakValueDictionary()
        self._host: object | None = None
        self._start_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._closing = False
        self._owner_loop: asyncio.AbstractEventLoop | None = None

    @property
    def started(self) -> bool:
        """Whether this original owner completed startup and is not closing."""
        task = self._start_task
        return (not self._closing and task is not None and task.done()
                and not task.cancelled() and task.exception() is None)

    @property
    def declared_tools(self) -> frozenset[str]:
        return frozenset(name for provider in self.providers for name in provider.tool_names)

    @property
    def declared_commands(self) -> frozenset[str]:
        """Inert native command discovery; unrelated local controls need no owner startup."""
        return frozenset(name for provider in self.providers for name in getattr(provider, "command_names", ()))

    @property
    def declared_skills(self) -> frozenset[str]:
        """Inert optional discoverable names, independent of skill body scanning.

        A declaration does not require a tool/runtime/extension or activate a
        skill. SkillManager still discovers packaged skills independently.
        """
        return frozenset(name for provider in self.providers
                         for name in (*getattr(provider, "skill_names", ()),
                                      *getattr(provider, "default_skills", ())))

    @property
    def default_skills(self) -> frozenset[str]:
        """Inert skill defaults from live bindings after successful owner startup only."""
        task = self._start_task
        if self._closing or task is None or not task.done() or task.cancelled() or task.exception() is not None:
            return frozenset()
        return frozenset(name for provider in self.providers if provider.name in self._bindings
                         for name in getattr(provider, "default_skills", ()))

    @property
    def requirements(self) -> frozenset[str]:
        return frozenset(name for provider in self.providers for name in provider.requirements)

    @property
    def optional_requirements(self) -> frozenset[str]:
        """Requested integrations whose absence does not disable core capability data."""
        return frozenset(name for provider in self.providers
                         for name in getattr(provider, "optional_requirements", ()))

    def get(self, name: str) -> Any:
        """Diagnostic access; application execution must not branch on feature IDs."""
        return self._bindings.get(name)

    async def start(self, host: object) -> None:
        if self._closing:
            raise RuntimeError("Capability owner is closed")
        if self._host is not None and self._host is not host:
            raise RuntimeError("Capability host owner changed")
        self._host = host
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._open(host), name="capability-start")
        await wait_owned(self._start_task)

    async def _open(self, host: object) -> None:
        self._owner_loop = asyncio.get_running_loop()
        try:
            for provider in self.providers:
                if self._closing:
                    break
                binding = await provider.open(host)
                if binding is None or not callable(getattr(binding, "aclose", None)):
                    raise ValueError("Installed capability returned an invalid binding")
                self._bindings[provider.name] = binding
            self.tools  # Validate the complete published inventory before success.
        except BaseException as error:
            try:
                await self._close_bindings()
            except BaseException as cleanup:
                raise BaseExceptionGroup("Capability startup and cleanup failed", [error, cleanup]) from None
            raise

    def attach(self, parent: ParentPort) -> None:
        if self._closing:
            raise RuntimeError("Capability owner is closed")
        if not parent.valid():
            raise ValueError("Capability parent owner is stale")
        for binding in self._bindings.values():
            attach = getattr(binding, "attach", None)
            if attach is not None:
                attach(parent)

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        result = []
        names: set[str] = set()
        active: set[int] = set()
        for binding in self._bindings.values():
            for definition in binding.tools:
                if definition.name in names:
                    raise ValueError("Duplicate capability tool name")
                names.add(definition.name)
                if isinstance(definition, AgentTool):
                    tool = definition
                else:
                    key = id(definition)
                    active.add(key)
                    previous = self._tools.get(key)
                    if previous is None or previous[2] is not binding:
                        async def execute(*args: Any, _definition: Any = definition, **kwargs: Any) -> AgentToolResult:
                            value = await _definition.execute(*args, **kwargs)
                            return value if isinstance(value, AgentToolResult) else AgentToolResult.from_text(value)
                        tool = AgentTool(name=definition.name, description=definition.description,
                                         parameters=definition.parameters, execute=execute)
                        self._tools[key] = definition, tool, binding
                    else:
                        tool = previous[1]
                self._owned[id(tool)] = tool
                result.append(tool)
        self._tools = {key: value for key, value in self._tools.items() if key in active}
        return tuple(result)

    @property
    def owned_tools(self) -> tuple[AgentTool, ...]:
        """Weak identities let a retiring host remove only its original tools."""
        return tuple(self._owned.values())

    def owns_tool(self, tool: AgentTool) -> bool:
        return self._owned.get(id(tool)) is tool

    def tool_effect(self, tool: AgentTool, args: dict[str, Any]) -> ToolEffect | None:
        """Ask only a live original binding; discovery never starts an owner."""
        task = self._start_task
        if (self._closing or task is None or not task.done() or task.cancelled()
                or task.exception() is not None or not isinstance(tool, AgentTool)):
            return None
        for binding in self._bindings.values():
            for definition in binding.tools:
                if isinstance(definition, AgentTool):
                    matches = definition is tool
                else:
                    original = self._tools.get(id(definition))
                    matches = (original is not None and original[0] is definition
                               and original[1] is tool and original[2] is binding)
                if not matches:
                    continue
                classify = getattr(binding, "tool_effect", None)
                if classify is None:
                    return None
                effect = classify(definition, args)
                if effect is not None and not isinstance(effect, ToolEffect):
                    raise TypeError("Capability tool_effect must return ToolEffect or None")
                return effect
        return None

    @property
    def owner_loop(self) -> asyncio.AbstractEventLoop:
        """Existing provider loop; querying this never creates a loop or owner."""
        if self._owner_loop is None or self._owner_loop.is_closed() or self._closing:
            raise ValueError("Capability owner loop is unavailable")
        return self._owner_loop

    def _child_binding(self, actual_tool: AgentTool) -> Any:
        task = self._start_task
        if (self._closing or task is None or not task.done() or task.cancelled()
                or task.exception() is not None):
            return None
        # Re-read the live inventory. owned_tools also contains retired identities
        # and is intentionally NOT sufficient evidence for delegation.
        for binding in self._bindings.values():
            for definition in binding.tools:
                if isinstance(definition, AgentTool):
                    matches = definition is actual_tool
                else:
                    cached = self._tools.get(id(definition))
                    matches = (cached is not None and cached[0] is definition
                               and cached[1] is actual_tool and cached[2] is binding)
                if matches:
                    return binding
        return None

    def can_delegate_child_tool(self, actual_tool: AgentTool) -> bool:
        """Exact live identity query, never a tool-name or discovery-time grant."""
        binding = self._child_binding(actual_tool)
        return binding is not None and callable(getattr(binding, "delegate_child_tool", None))

    async def delegate_child_tool(self, actual_tool: AgentTool, spec: ChildSpec,
                                  scope: dict[str, Any]) -> AgentTool:
        """Validate/build on the original provider loop; execute there as well.

        The returned executor is NOT a role-bound tool and has no permission or
        read-only exemption. Hosts must protect it against the original actual_tool.
        """
        from xdog.agent.child_config import child_tool_scopes
        from xdog.agent.child_policy import _matches
        from xdog.agent.cross_loop import settle_on

        data = child_tool_scopes({actual_tool.name: scope}, spec.tools)[actual_tool.name]
        binding = self._child_binding(actual_tool)
        if binding is None or not callable(getattr(binding, "delegate_child_tool", None)):
            raise ValueError("Original capability tool does not support child scope delegation")
        factory = binding.delegate_child_tool
        loop = self.owner_loop
        schema = copy.deepcopy(actual_tool.parameters)
        original_validator = actual_tool.validate_arguments
        def check() -> None:
            if (self._child_binding(actual_tool) is not binding
                    or getattr(binding, "delegate_child_tool", None) != factory):
                raise ValueError("Original capability tool owner/identity retired")
        async def build() -> AgentTool:
            check()
            result = await factory(actual_tool, replace(spec, tool_scopes=copy.deepcopy(spec.tool_scopes)),
                                   copy.deepcopy(data))
            check()
            if not isinstance(result, AgentTool) or result.name != actual_tool.name or result.execute is None:
                raise ValueError("Capability child factory must return the same tool identity name with an executor")
            return result
        scoped = await settle_on(loop, build())
        scoped_schema = copy.deepcopy(scoped.parameters)
        async def validate_on_owner(params: dict[str, Any]) -> None:
            check()
            seen: set[int] = set()
            for validator in (original_validator, actual_tool.validate_arguments, scoped.validate_arguments):
                if validator is not None and id(validator) not in seen:
                    seen.add(id(validator))
                    value = validator(params)
                    if inspect.isawaitable(value):
                        await value
                    check()
            if not all(_matches(candidate, params) for candidate in
                       (schema, actual_tool.parameters, scoped_schema, scoped.parameters)):
                raise ValueError("Scoped child arguments exceed original/current capability schema")
        async def validate(params: dict[str, Any]) -> None:
            await settle_on(loop, validate_on_owner(params))
        async def execute(call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                          on_update: Any = None, *, ctx: dict[str, Any] | None = None) -> AgentToolResult:
            if cancel is not None and cancel.is_set():
                raise asyncio.CancelledError
            caller_loop = asyncio.get_running_loop()
            async def update(result: AgentToolResult) -> None:
                if on_update is not None:
                    async def deliver() -> None:
                        await on_update(result)
                    await settle_on(caller_loop, deliver())
            async def invoke() -> AgentToolResult:
                check()
                # Host protect runs validate_arguments and then rechecks permission
                # on this same loop before entering execute. Do not repeat async
                # validation here and carry that permission across another await.
                if not all(_matches(candidate, params) for candidate in
                           (schema, actual_tool.parameters, scoped_schema, scoped.parameters)):
                    raise ValueError("Scoped child arguments exceed original/current capability schema")
                assert scoped.execute is not None
                # Cancellation/settlement uses the bridge, not a foreign-loop Event.
                return await scoped.execute(call_id, params, None, update, ctx=ctx)
            return await settle_on(loop, invoke())
        return replace(scoped, parameters=copy.deepcopy(scoped_schema), execute=execute, validate_arguments=validate)

    async def flush(self) -> None:
        if self._closing:
            raise RuntimeError("Capability owner is closed")
        if self._start_task is not None:
            await wait_owned(self._start_task)
        for binding in self._bindings.values():
            flush = getattr(binding, "flush", None)
            if flush is not None:
                await flush()

    async def _close_bindings(self) -> None:
        bindings = tuple(reversed(tuple(self._bindings.values())))
        try:
            await settle_cleanup(*(binding.aclose for binding in bindings),
                                 message="Installed capabilities failed to close")
        finally:
            self._bindings.clear()
            self._tools.clear()

    async def _close(self) -> None:
        if self._start_task is not None:
            try:
                await wait_owned(self._start_task)
            except BaseException:
                pass  # Startup reports its own failure after closing partial bindings.
        try:
            await self._close_bindings()
        finally:
            self._host = None

    async def aclose(self) -> None:
        self._closing = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="capability-close")
        await wait_owned(self._close_task)
