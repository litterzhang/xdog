"""Creation-time delegated capabilities; host policy still authorizes every effect.

No owner IDs, tools, callbacks or roots are accepted from persisted/model data.
This is a managed API boundary, not filesystem or shell isolation.
"""
from __future__ import annotations

import asyncio
import copy
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, cast

from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import NoSuchResource
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.agent.skills.manager import SkillManager, is_valid_skill_name
from xdog.agent.skills.render import render_skill_body
from xdog.agent.skills.tool import _Reader
from xdog.agent.skills.types import Skill
from xdog.agent.types import BeforeToolCallContext


def _deny_reference(uri: str) -> Any:
    raise NoSuchResource(uri)


def _matches(schema: dict[str, Any], args: Any) -> bool:
    # Delegation is an authority boundary: the lightweight provider argument
    # checker intentionally tolerates extra keys and is insufficient here.
    # Resolve local schema references only, never fetch documents from a tool.
    try:
        validator = validator_for(schema)
        validator.check_schema(schema)
        return bool(validator(schema, registry=cast(Any, Registry)(retrieve=_deny_reference)).is_valid(args))
    except Exception:
        return False


def _native_catalog(manager: SkillManager) -> bool:
    # Custom/host-restricted catalogs remain authoritative, including an
    # instance-provided catalog callback. Only the exact built-in manager can
    # use its bounded filesystem reader directly.
    return type(manager) is SkillManager and 'list_skills' not in vars(manager)


@dataclass(frozen=True)
class ChildDelegation:
    tools: tuple[AgentTool, ...]
    actions: tuple[tuple[str, tuple[str, ...]], ...] = ()
    schemas: tuple[dict[str, Any], ...] = field(default=(), repr=False, compare=False)

    @classmethod
    def capture(cls, available: Sequence[AgentTool], requested: tuple[str, ...] | None,
                actions: tuple[tuple[str, tuple[str, ...]], ...] = ()) -> ChildDelegation:
        original = tuple(available)
        names = [tool.name for tool in original]
        if len(names) != len(set(names)):
            raise ValueError('Ambiguous parent tool identity; delegation refused')
        chosen = set(names if requested is None else requested)
        if requested is not None and len(requested) != len(set(requested)):
            raise ValueError("Duplicate child tool selection")
        if not chosen <= set(names):
            raise ValueError('Child tool profile exceeds parent delegable active tools')
        if any(name not in chosen for name, _ in actions):
            raise ValueError('Child action restriction names a non-delegated tool')
        tools = tuple(tool for tool in original if tool.name in chosen)
        return cls(tools, actions, tuple(copy.deepcopy(tool.parameters) for tool in tools))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tools)

    def check(self, ctx: BeforeToolCallContext, current: Sequence[AgentTool], *, live: bool,
              capability_bound: bool = False) -> None:
        if not live:
            raise ValueError('Child parent owner retired; no successor authority')
        if capability_bound:
            # Exact original-host capability binding is checked by the host; action
            # narrowing still applies, even to explicitly rebound child tools.
            allowed = dict(self.actions).get(ctx.tool_call.name)
            if allowed is not None and ctx.args.get("action") not in allowed:
                raise ValueError("Child tool action is outside delegated permissions")
            return
        source = next((tool for tool in self.tools if tool.name == ctx.tool_call.name), None)
        if source is None or not any(tool is source for tool in current):
            raise ValueError('Child tool is outside its original/current parent delegation')
        schema = self.schemas[self.tools.index(source)] if self.schemas else source.parameters
        if not (_matches(schema, ctx.args)
                and _matches(source.parameters, ctx.args)):
            raise ValueError('Child arguments exceed the delegated parent tool schema')
        allowed = dict(self.actions).get(source.name)
        if allowed is not None and ctx.args.get('action') not in allowed:
            raise ValueError('Child tool action is outside delegated permissions')


class ChildSkills(SkillManager):
    """Detached selected instruction bodies, never the parent's writable skill manager."""
    def __init__(self, parent: SkillManager | None, selected: tuple[str, ...]) -> None:
        if len(selected) != len(set(selected)) or any(not is_valid_skill_name(slug) for slug in selected):
            raise ValueError("Invalid delegated skill selection")
        self._parent = parent
        self._selected = selected
        self._rows: dict[str, Skill] = {}
        if parent is None:
            if selected:
                raise ValueError('Parent has no skills available to delegate')
            return
        if not selected:
            return
        if not _native_catalog(parent) and not set(selected) <= {row.slug for row in parent.list_skills()}:
            raise ValueError("Child skills exceed parent available catalog")
        reader = _Reader(parent, frozenset(selected), 262144, 512)
        try:
            self._rows = {slug: reader.load(slug, threading.Event()) for slug in selected}
        except (ValueError, OSError, UnicodeError) as exc:
            raise ValueError("Child skills exceed parent available catalog or are unsafe/unavailable") from exc
        if sum(len(row.content.encode()) for row in self._rows.values()) > 1024 * 1024:
            raise ValueError('Delegated skill bodies exceed instruction capacity')

    @property
    def slugs(self) -> tuple[str, ...]:
        return self._selected

    def check_live(self, parent: SkillManager | None) -> None:
        if not self._selected:
            return
        if parent is not self._parent:
            raise ValueError('Parent skill catalog changed; delegate again')
        if parent is not None:
            if (not _native_catalog(parent)
                    and not set(self._selected) <= {row.slug for row in parent.list_skills()}):
                raise ValueError("Delegated skill revoked from parent catalog")
            reader = _Reader(parent, frozenset(self._selected), 262144, 512)
            for slug, saved in self._rows.items():
                if reader.load(slug, threading.Event()) != saved:
                    raise ValueError('Delegated skill changed or was revoked; delegate again')

    def list_skills(self) -> list[Skill]:
        return [replace(row, content='') for row in self._rows.values()]

    def load_skill(self, slug: str) -> Skill | None:
        return self._rows.get(slug)

    def save_skill(self, slug: str, content: str, *, name: str = '', description: str = '',
                   scope: str = 'shared') -> Skill:
        raise PermissionError('Delegated skill catalog is read-only')

    def patch_skill(self, slug: str, patch_text: str) -> Skill | None:
        raise PermissionError('Delegated skill catalog is read-only')

    def remove_skill(self, slug: str) -> bool:
        raise PermissionError('Delegated skill catalog is read-only')

    def tool(self, source: AgentTool, check_owner: Callable[[], bool]) -> AgentTool:
        async def execute(call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                          on_update: Any = None, **kwargs: Any) -> AgentToolResult:
            if cancel is not None and cancel.is_set():
                raise asyncio.CancelledError
            if not check_owner():
                raise ValueError('Child skill owner retired')
            if params.get('action') == 'list' and set(params) == {'action'}:
                value = '\n'.join(f'{row.slug}: {row.description}' for row in self.list_skills())
            elif params.get('action') == 'load' and set(params) == {'action', 'slug'}:
                skill = self.load_skill(params['slug'])
                if skill is None:
                    raise ValueError('Skill is outside delegated catalog')
                value = render_skill_body(skill)
            else:
                raise ValueError('Child skills permit only delegated list/load, not parent skill management')
            return AgentToolResult.from_text(value)
        parameters = copy.deepcopy(source.parameters)
        properties = parameters.get('properties', {})
        if 'action' in properties:
            properties['action'] = {'type': 'string', 'enum': ['list', 'load']}
        return replace(source, parameters=parameters,
                       description='List/load only this child\'s delegated skills; no parent skill writes.',
                       execute=execute)


@dataclass(frozen=True, init=False, eq=False)
class ParentHandle:
    """Host-issued live parent capability, never a parent conversation/control API.

    These callbacks are trusted host injections, not model or snapshot data.
    The handle neither starts tasks nor retains a raw parent Agent. Hosts fence
    the callbacks to their original owner/epoch and authorize actual effects.
    """

    _alive: Callable[[], bool] = field(repr=False, compare=False)
    _tools: Callable[[], Sequence[AgentTool]] = field(repr=False, compare=False)
    _skills: Callable[[], SkillManager | None] = field(repr=False, compare=False)

    def __init__(self, *, alive: Callable[[], bool], tools: Callable[[], Sequence[AgentTool]],
                 skills: Callable[[], SkillManager | None]) -> None:
        object.__setattr__(self, '_alive', alive)
        object.__setattr__(self, '_tools', tools)
        object.__setattr__(self, '_skills', skills)

    @property
    def live(self) -> bool:
        return self._alive()

    def _check_live(self) -> None:
        if not self.live:
            raise ValueError('Child parent owner retired; no successor authority')

    def capture_tools(self, requested: tuple[str, ...] | None,
                      actions: tuple[tuple[str, tuple[str, ...]], ...] = ()) -> ChildDelegation:
        self._check_live()
        result = ChildDelegation.capture(self._tools(), requested, actions)
        self._check_live()
        return result

    def check_tool(self, grant: ChildDelegation, ctx: BeforeToolCallContext, *,
                   capability_bound: bool = False) -> None:
        grant.check(ctx, self._tools(), live=self.live, capability_bound=capability_bound)

    def skill_catalog(self) -> tuple[str, ...]:
        self._check_live()
        manager = self._skills()
        if manager is not None and _native_catalog(manager):
            listed = _Reader(manager, None, 262144, 512).listing(threading.Event())
            if listed['unavailable'] or listed['truncated']:
                raise ValueError('Parent skill catalog is unsafe/unavailable or exceeds selection capacity')
            result = tuple(row['slug'] for row in listed['skills'])
        else:
            result = tuple(row.slug for row in manager.list_skills()) if manager is not None else ()
        self._check_live()
        return result

    def capture_skills(self, selected: tuple[str, ...] | None) -> ChildSkills:
        self._check_live()
        result = ChildSkills(self._skills(), self.skill_catalog() if selected is None else selected)
        self._check_live()
        return result

    def check_skills(self, grant: ChildSkills) -> None:
        self._check_live()
        grant.check_live(self._skills())
        self._check_live()
