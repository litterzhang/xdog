"""Host-owned tool registration SDK, without a process-global runtime.

Construct a registry for an application/owner. Definitions create fresh tools;
source bindings contain that owner's actual tools. The caller owns all resources,
permissions, publication timing and registration lifetime. Import is inert.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from xdog.agent.core import AgentTool

if TYPE_CHECKING:
    from xdog.agent.agent import Agent


@dataclass(frozen=True)
class ToolContext:
    """Explicit factory input, not permission or a reference to another Agent."""

    cwd: Path | None = None
    image_model: str = ''


ToolFactory = Callable[[ToolContext], AgentTool]


def _name(value: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError('Tool/source names must be nonempty text without surrounding whitespace')
    return value


@dataclass(frozen=True)
class ToolRegistration:
    """Dispose only this source revision; a stale handle cannot remove its replacement."""

    _registry: ToolRegistry
    _source: str
    _token: object

    def close(self) -> None:
        self._registry._remove(self._source, self._token)


class ToolRegistry:
    """One caller-owned factory catalog and actual tool inventory.

    No model, event loop, worker, automatic discovery or global state is created.
    A host must not give untrusted code another owner's registry. Registration is
    not enablement or authorization; executor fencing remains with the provider.
    """

    def __init__(self, *, builtins: bool = True) -> None:
        if type(builtins) is not bool:
            raise TypeError('builtins must be a bool')
        self._factories: dict[str, ToolFactory] = {}
        self._defaults: list[str] = []
        self._sources: dict[str, tuple[object, tuple[AgentTool, ...]]] = {}
        if builtins:
            from xdog.agent.tools.tool_bash import create_bash_tool
            from xdog.agent.tools.tool_current_time import create_current_time_tool
            from xdog.agent.tools.tool_filesystem import create_filesystem_tool
            from xdog.agent.tools.tool_submit_result import create_submit_result_tool

            self.register_factory('bash', lambda context: create_bash_tool(initial_cwd=context.cwd), default=True)
            self.register_factory('filesystem', lambda context: create_filesystem_tool(), default=True)
            self.register_factory('current_time', lambda context: create_current_time_tool(), default=True)
            self.register_factory('submit_result', lambda context: create_submit_result_tool())

    def register_factory(self, name: str, factory: ToolFactory, *, default: bool = False) -> None:
        """Add a definition, rejecting duplicate names instead of import-order overrides."""
        _name(name)
        if not callable(factory) or type(default) is not bool:
            raise TypeError('A tool factory must be callable and default must be a bool')
        if name in self._factories:
            raise ValueError(f'Tool factory already registered: {name}')
        self._factories[name] = factory
        if default:
            self._defaults.append(name)

    def registered_names(self) -> frozenset[str]:
        """Factory names; actual bound inventory is exposed separately by tools."""
        return frozenset(self._factories)

    def default_names(self) -> tuple[str, ...]:
        return tuple(self._defaults)

    def create_tools(self, names: Iterable[str] | None = None, *,
                     context: ToolContext | None = None) -> list[AgentTool]:
        """Create fresh instances; None selects defaults, an empty iterable selects none.

        Creating instances does not publish them. The host binds them as a source
        after it has finished preparation. Unknown/duplicate names fail before any
        factory is invoked.
        """
        if isinstance(names, str):
            raise TypeError('Tool names must be an iterable of names, not a string')
        selected = tuple(self._defaults if names is None else names)
        for name in selected:
            _name(name)
        if len(set(selected)) != len(selected):
            raise ValueError('Duplicate selected tool name')
        unknown = set(selected) - self._factories.keys()
        if unknown:
            raise ValueError('Unknown tools: ' + ', '.join(sorted(unknown)))
        ctx = ToolContext() if context is None else context
        if not isinstance(ctx, ToolContext):
            raise TypeError('Expected ToolContext')
        result = []
        for name in selected:
            tool = self._factories[name](ctx)
            if not isinstance(tool, AgentTool) or tool.name != name:
                raise ValueError(f'Tool factory must return an AgentTool named {name}')
            result.append(tool)
        return result

    def replace_source(self, source: str, tools: Iterable[AgentTool]) -> ToolRegistration:
        """Atomically replace one source; all visible names must remain unique."""
        _name(source)
        definitions = tuple(tools)
        occupied = {tool.name for name, (_, values) in self._sources.items() if name != source for tool in values}
        seen: set[str] = set()
        for tool in definitions:
            if not isinstance(tool, AgentTool):
                raise TypeError('Only actual AgentTool instances can be bound')
            _name(tool.name)
            if tool.name in occupied or tool.name in seen:
                raise ValueError(f'Duplicate tool name across registry sources: {tool.name}')
            seen.add(tool.name)
        token = object()
        self._sources[source] = token, definitions
        return ToolRegistration(self, source, token)

    def _remove(self, source: str, token: object) -> None:
        current = self._sources.get(source)
        if current is not None and current[0] is token:
            del self._sources[source]

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        return tuple(tool for _, definitions in self._sources.values() for tool in definitions)

    def publish(self, agent: Agent) -> None:
        """Publish on the host's original execution owner; never broaden selection."""
        replacement = self.tools
        if tuple(map(id, agent.local_tools)) != tuple(map(id, replacement)):
            agent.set_tools(replacement)
