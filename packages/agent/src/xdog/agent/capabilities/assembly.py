"""SDK composition of explicitly opened capabilities, without a host runtime.

Hosts still own provider startup/close, permissions, routing, persistence and
child grants. This object only assembles actual tools and skill selections.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from xdog.agent.capabilities.runtime import CapabilitySet
from xdog.agent.core import AgentTool
from xdog.agent.tool_effects import ToolEffect
from xdog.agent.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from xdog.agent.agent import Agent
    from xdog.agent.core import AgentConfig, AgentMessage, StreamFn
    from xdog.agent.skills import SkillManager


class CapabilityAssembly:
    """A live projection of caller-owned sets, not another resource owner.

    It performs no discovery or IO itself. Recreate the projection when a host
    replaces a scope's CapabilitySet; application bindings can remain shared.
    Only actual tool identity is used for retirement and effect lookup.
    """

    def __init__(self, *sets: CapabilitySet) -> None:
        if any(not isinstance(value, CapabilitySet) for value in sets):
            raise TypeError('CapabilityAssembly requires CapabilitySet instances')
        self.sets = tuple(dict.fromkeys(sets))

    @property
    def declared_tools(self) -> frozenset[str]:
        return frozenset(name for source in self.sets for name in source.declared_tools)

    @property
    def declared_skills(self) -> frozenset[str]:
        """Optional provider metadata; not activation, installation or authority."""
        return frozenset(name for source in self.sets for name in source.declared_skills)

    @property
    def default_skills(self) -> frozenset[str]:
        return frozenset(name for source in self.sets for name in source.default_skills)

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        tools = tuple(tool for source in self.sets for tool in source.tools)
        if len({tool.name for tool in tools}) != len(tools):
            raise ValueError('Duplicate tool name across capability scopes')
        return tools

    def owns_tool(self, tool: AgentTool) -> bool:
        return any(source.owns_tool(tool) for source in self.sets)

    def active_skills(self, selected: Iterable[str] = ()) -> frozenset[str]:
        """Select instructions only, never grant tools or change approvals."""
        return frozenset(selected) | self.default_skills

    def compose_tools(self, existing: Iterable[AgentTool] = (), *,
                      enabled_tools: Iterable[str] | None = None) -> tuple[AgentTool, ...]:
        """Replace only our owned handles; never shadow another tool.

        enabled_tools selects capability tools, not supplied base tools. Empty
        grants none; None selects all. Ambiguous names fail even when filtered.
        """
        published = self.tools
        allowed = None if enabled_tools is None else frozenset(enabled_tools)
        base = tuple(tool for tool in existing if not self.owns_tool(tool))
        additions = tuple(tool for tool in published if allowed is None or tool.name in allowed)
        result = (*base, *additions)
        if len({tool.name for tool in result}) != len(result):
            raise ValueError('Capability tool name already owned; refusing replacement')
        registry = ToolRegistry(builtins=False)
        registry.replace_source('inventory', result)
        return registry.tools

    def publish_tools(self, agent: Agent, *, enabled_tools: Iterable[str] | None = None) -> None:
        """Publish on the existing Agent; retain explicit active-tool selection.

        Hosts first fence their actual owner and ensure publication is legal.
        Equivalent inventory does not rewrite selection or tool revisions.
        """
        original = agent.local_tools
        replacement = self.compose_tools(original, enabled_tools=enabled_tools)
        if (len(original) != len(replacement)
                or {id(tool) for tool in original} != {id(tool) for tool in replacement}):
            agent.set_tools(replacement)

    def tool_effect(self, tool: AgentTool, args: dict[str, Any]) -> ToolEffect | None:
        for source in self.sets:
            # The set checks its live binding by actual identity. owned_tools
            # also contains retired handles and is not execution authority.
            effect = source.tool_effect(tool, args)
            if effect is not None:
                return effect
        return None

    def create_agent(self, stream_fn: StreamFn, *, skills: SkillManager,
                     config: AgentConfig | None = None, tools: Iterable[AgentTool] = (),
                     active_skills: Iterable[str] = (), enabled_tools: Iterable[str] | None = None,
                     **kwargs: Any) -> Agent:
        """Build a native Agent using the same assembly as product hosts.

        Sets must already be opened by the calling application. This factory
        does not own them, create an ACP endpoint, install permissions, or grant
        capabilities to child agents. Other kwargs go to Agent unchanged.
        """
        from xdog.agent.agent import Agent

        self._require_started()
        return Agent(stream_fn, config=config, tools=self.compose_tools(tools, enabled_tools=enabled_tools),
                     skills=skills, active_skills=tuple(sorted(self.active_skills(active_skills))), **kwargs)

    def _require_started(self) -> None:
        if any(not source.started for source in self.sets):
            raise RuntimeError('Start each capability set before creating an Agent or restoring it')

    def restore_agent(self, agent: Agent, snapshot: Mapping[str, Any], *,
                      allowed_models: Sequence[str] | None = None) -> None:
        """Atomically restore with current live defaults, without publishing tools.

        The Agent retains its actual executors, permission hooks, tool selection
        and model adapter. Original snapshot skill bindings are checked before
        session defaults absent from saved data are added. Consumed turn skills stay
        consumed. No resources, model calls or execution loops are created.
        """
        self._require_started()
        agent.restore(snapshot, allowed_models=allowed_models, default_skills=tuple(sorted(self.default_skills)))

    def restore_agent_parts(self, agent: Agent, metadata: Mapping[str, Any], messages: Sequence[AgentMessage], *,
                            allowed_models: Sequence[str] | None = None) -> None:
        """The same explicit reassembly contract for separately stored history."""
        self._require_started()
        agent.restore_parts(metadata, messages, allowed_models=allowed_models,
                            default_skills=tuple(sorted(self.default_skills)))
