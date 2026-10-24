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
from xdog.agent.tools.repo import ToolRepo

if TYPE_CHECKING:
    from xdog.agent.agent import Agent
    from xdog.agent.core import AgentConfig, AgentMessage, StreamFn
    from xdog.agent.skills import SkillRepo


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
        self._tool_repo = ToolRepo(builtin=False)
        self._contributed: tuple[AgentTool, ...] = ()
        self._skill_repo: SkillRepo | None = None

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

    @property
    def tool_repo(self) -> ToolRepo:
        incoming = ToolRepo(builtin=False)
        for source in self.sets:
            incoming = incoming.merge(source.tool_repo)
        incoming = incoming.merge(ToolRepo(*self.tools, builtin=False))
        previous = {id(tool) for tool in self._contributed}
        local = ToolRepo(*(tool for tool in self._tool_repo if id(tool) not in previous), builtin=False)
        updated = local.merge(incoming)  # Validate before changing the live collection.
        # Retire only changed capability contributions. Local registration
        # tokens stay valid across unrelated capability inventory refreshes.
        for tool in self._contributed:
            if (tool.name in self._tool_repo.names() and self._tool_repo.get(tool.name) is tool
                    and (tool.name not in incoming.names() or incoming.get(tool.name) is not tool)):
                self._tool_repo.unregister(tool.name)
        for tool in updated:
            if tool.name not in self._tool_repo.names():
                self._tool_repo.register(tool)
        self._contributed = incoming.tools
        return self._tool_repo

    @property
    def skill_repo(self) -> SkillRepo:
        from xdog.agent.skills import SkillRepo

        if self._skill_repo is None:
            self._skill_repo = SkillRepo(builtin=False).merge(*(source.skill_repo for source in self.sets))
        return self._skill_repo

    def compose_tools(self, existing: Iterable[AgentTool] = ()) -> tuple[AgentTool, ...]:
        """Retire only our original handles; all selected capability tools remain."""
        base = ToolRepo(*(tool for tool in existing if not self.owns_tool(tool)), builtin=False)
        return base.merge(self.tool_repo).tools

    def publish_tools(self, agent: Agent) -> None:
        """Publish an idle owner's inventory without broadening active selection."""
        original = agent.local_tools
        replacement = self.compose_tools(original)
        if tuple(map(id, original)) != tuple(map(id, replacement)):
            agent.set_tools(replacement)

    def tool_effect(self, tool: AgentTool, args: dict[str, Any]) -> ToolEffect | None:
        for source in self.sets:
            # The set checks its live binding by actual identity. owned_tools
            # also contains retired handles and is not execution authority.
            effect = source.tool_effect(tool, args)
            if effect is not None:
                return effect
        return None

    def create_agent(self, stream_fn: StreamFn, *, tool_repo: ToolRepo | None = None,
                     skill_repo: SkillRepo | None = None, config: AgentConfig | None = None,
                     **kwargs: Any) -> Agent:
        """Create a native Agent; supplied repos ADD to the selected capabilities.

        No second enable list, no local override of capability contributions.
        Native Agent options (including explicit body activation) pass through;
        resource lifetime, permission hooks and actual executors stay host-owned.
        """
        from xdog.agent.agent import Agent
        from xdog.agent.skills import create_skill_tool

        self._require_started()
        if 'tools' in kwargs or 'skills' in kwargs or 'enabled_tools' in kwargs or 'enabled_skills' in kwargs:
            raise TypeError('Capability assembly accepts additive tool_repo and skill_repo, not enable lists')
        tools = self.tool_repo.merge()  # Per-Agent readers must never pollute the shared repo.
        if tool_repo is not None:
            tools = tools.merge(tool_repo)
        skills = self.skill_repo
        if skill_repo is not None:
            skills = skills.merge(skill_repo)
        missing = sorted(slug for slug in self.declared_skills if skills.load_skill(slug) is None)
        if missing:
            from xdog.agent.skills import SkillRepo
            skills = skills.merge(SkillRepo.from_packaged(missing))
        selected = self.active_skills(kwargs.pop('active_skills', ()))
        if skills.list_skills() and 'skill' not in tools.names():
            tools.register(create_skill_tool(skills))
        return Agent(stream_fn, config=config, tools=tools.tools, skills=skills,
                     active_skills=tuple(sorted(selected)), **kwargs)

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
