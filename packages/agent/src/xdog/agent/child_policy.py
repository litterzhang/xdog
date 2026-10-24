"""Creation-time delegated capabilities; host policy still authorizes every effect.

No owner IDs, tools, callbacks or roots are accepted from persisted/model data.
This is a managed API boundary, not filesystem or shell isolation.
"""
from __future__ import annotations

import asyncio
import copy
import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import NoSuchResource
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.agent.skills.render import render_skill_body
from xdog.agent.skills.repo import SkillRepo, is_valid_skill_name
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


def _schema_intersection(scoped: Any, parent: Any) -> Any:
    """Keep scoped inventory narrow while intersecting common JSON-schema fields.

    Unknown/conflicting constraints are retained with allOf, never discarded.
    References/definitions need their original document roots and are refused
    rather than accidentally resolving against a different delegation document.
    """
    if scoped == parent:
        return copy.deepcopy(scoped)
    if scoped is False or parent is False:
        return False
    if scoped is True:
        return copy.deepcopy(parent)
    if parent is True:
        return copy.deepcopy(scoped)
    if not isinstance(scoped, dict) or not isinstance(parent, dict):
        raise ValueError("Invalid scoped tool schema")
    if any(key in schema for schema in (scoped, parent) for key in ("$ref", "$defs", "definitions", "$id")):
        raise ValueError("Scoped schema intersection requires independent inline schemas")
    # Literal candidates are a complete finite set; filter instead of publishing
    # the parent's broader enum (including non-delegated control actions).
    candidates = scoped.get("enum", parent.get("enum"))
    if isinstance(candidates, list):
        result = copy.deepcopy(scoped)
        result["enum"] = [copy.deepcopy(value) for value in candidates
                          if _matches(scoped, value) and _matches(parent, value)]
        if not result["enum"]:
            raise ValueError("Scoped tool schema has an empty literal intersection")
        return result
    result = copy.deepcopy(scoped)
    extra = []
    for key, value in parent.items():
        if key == "properties" and isinstance(value, dict):
            properties = result.setdefault("properties", {})
            if not isinstance(properties, dict):
                raise ValueError("Invalid scoped tool schema properties")
            for name, constraint in value.items():
                if name in properties:
                    properties[name] = _schema_intersection(properties[name], constraint)
                elif scoped.get("additionalProperties") is not False:
                    properties[name] = _schema_intersection(
                        scoped.get("additionalProperties", True), constraint)
        elif key == "required":
            result[key] = sorted(set(result.get(key, ())) | set(value))
        elif key in {"description", "title", "default", "examples"}:
            continue  # Scoped metadata must not advertise the parent's controls.
        elif key not in result:
            result[key] = copy.deepcopy(value)
        elif result[key] != value:
            if key in {"items", "additionalProperties"}:
                result[key] = _schema_intersection(result[key], value)
            else:
                extra.append({key: copy.deepcopy(value)})
    # additionalProperties also constrains properties newly named by the other
    # document, including schemas that originally declared no properties at all.
    if isinstance(result.get("properties"), dict):
        parent_properties = parent.get("properties", {})
        additional = parent.get("additionalProperties", True)
        for name in set(result["properties"]) - set(parent_properties):
            if additional is False:
                result["properties"].pop(name)
            elif additional is not True:
                result["properties"][name] = _schema_intersection(result["properties"][name], additional)
    if extra:
        # Do not overwrite an existing scoped allOf.
        result.setdefault("allOf", []).extend(extra)
    return result


def _validate_action_selection(tool: AgentTool, selected: tuple[str, ...]) -> None:
    """Reject unusable action profiles before a host creates/persists a child.

    This checks the declared discriminator, not satisfiability of every possible
    argument combination. The full captured AND live schemas still gate effects.
    No shell parsing, wildcard expansion or implicit permission repair occurs.
    """
    schema = tool.parameters
    properties = schema.get("properties", {})
    action_schema = properties.get("action") if isinstance(properties, dict) else None
    candidates = schema.get("enum")
    legacy_actions = (tuple(row.get("action") for row in candidates if isinstance(row, dict))
                      if isinstance(candidates, list) else None)
    if action_schema is None and not any(isinstance(value, str) for value in legacy_actions or ()):
        raise ValueError(
            f"Child tool_actions[{tool.name!r}] requires a tool with a declared 'action' parameter; "
            f"{tool.name} is not a multi-action tool. Omit its tool_actions entry (not the tools entry). "
            'For bash use {"tools": ["bash"], "tool_actions": {}}; command names such as sleep '
            "are command text, not actions. '*' is not a wildcard. "
            "Omission does not grant tools or bypass parent permissions/read-only access.")
    if len(selected) != len(set(selected)) or any(not isinstance(value, str) or not value for value in selected):
        raise ValueError("Child tool_actions requires distinct nonempty literal action strings")
    try:
        validator = validator_for(schema)
        validator.check_schema(schema)
        root = validator(schema, registry=cast(Any, Registry)(retrieve=_deny_reference))
        for action in selected:
            # evolve retains the root resolver for local action-schema references.
            if ((action_schema is not None and not root.evolve(schema=action_schema).is_valid(action))
                    or (legacy_actions is not None and action not in legacy_actions)):
                raise ValueError(f"Unsupported literal action {action!r}")
    except Exception as exc:
        raise ValueError(
            f"Invalid child tool_actions[{tool.name!r}]: selected actions must match the parent tool's "
            'declared action schema. Use literal actions (e.g. {"filesystem": ["read"]}); '
            "'*' is not a wildcard. "
            "Parent tool/approval/read-only restrictions still apply.") from exc


def _native_catalog(manager: SkillRepo) -> bool:
    # Custom/host-restricted catalogs remain authoritative, including an
    # instance-provided catalog callback. Only the exact built-in manager can
    # use its bounded filesystem reader directly.
    return type(manager) is SkillRepo and 'list_skills' not in vars(manager)


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
        if len({name for name, _ in actions}) != len(actions):
            raise ValueError('Duplicate child tool action restriction')
        tools = tuple(tool for tool in original if tool.name in chosen)
        by_name = {tool.name: tool for tool in tools}
        for name, selected in actions:
            _validate_action_selection(by_name[name], selected)
        return cls(tools, actions, tuple(copy.deepcopy(tool.parameters) for tool in tools))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tools)

    def provider_parameters(self, name: str) -> dict[str, Any]:
        """Return detached provider advertisement, never a grant of authority.

        Legacy scalar-valued object enums are advertised field by field, so the
        provider may accept combinations or omissions outside the original grant.
        The model is not a security boundary: hosts must still call ``check`` at
        actual execution to enforce the unchanged authoritative ``schemas``.
        Additional root keywords on legacy enums are refused rather than dropped.
        """
        source = next((tool for tool in self.tools if tool.name == name), None)
        if source is None:
            raise ValueError('Child tool is outside its delegation')
        schema = copy.deepcopy(self.schemas[self.tools.index(source)] if self.schemas else source.parameters)
        if schema.get('type') != 'object' or 'enum' not in schema:
            return schema
        if set(schema) != {'type', 'enum'}:
            raise ValueError('Legacy object enum has unsupported root constraints')
        candidates = schema['enum']
        if not isinstance(candidates, list) or not candidates:
            raise ValueError('Legacy object enum requires a nonempty list of objects')
        properties: dict[str, Any] = {}
        required: set[str] | None = None
        for candidate in candidates:
            if not isinstance(candidate, dict) or any(type(key) is not str for key in candidate):
                raise ValueError('Legacy object enum candidates must be JSON objects with string keys')
            required = set(candidate) if required is None else required.intersection(candidate)
            for key, value in candidate.items():
                value_type = {type(None): 'null', bool: 'boolean', int: 'integer',
                              float: 'number', str: 'string'}.get(type(value))
                if value_type is None or (type(value) is float and not math.isfinite(value)):
                    raise ValueError('Legacy object enum fields must be finite JSON scalars')
                prop = properties.setdefault(key, {'type': [], 'enum': []})
                if value_type not in prop['type']:
                    prop['type'].append(value_type)
                # JSON booleans and numbers are distinct, despite True == 1 in
                # Python. Integer/float representations of the same JSON number
                # are equal and must not create duplicate enum entries.
                if not any(value == old and (type(value) is bool) == (type(old) is bool)
                           for old in prop['enum']):
                    prop['enum'].append(value)
        for prop in properties.values():
            if len(prop['type']) == 1:
                prop['type'] = prop['type'][0]
        return {'type': 'object', 'properties': properties,
                'required': sorted(required or ()), 'additionalProperties': False}

    def scoped_parameters(self, name: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Advertise scoped fields/literals, intersected with the frozen grant/actions."""
        source = next((tool for tool in self.tools if tool.name == name), None)
        if source is None:
            raise ValueError("Scoped tool is outside its original delegation")
        parent_schema = self.schemas[self.tools.index(source)] if self.schemas else source.parameters
        # Ordinary provider normalization may lose legacy object-enum pairing.
        # Never turn that deliberately tolerant advertisement into an authority
        # snapshot for a newly scoped tool; unsupported legacy scopes fail closed.
        if parent_schema.get("type") == "object" and "enum" in parent_schema:
            raise ValueError("Scoped tools require inline property schemas, not legacy object enums")
        schema = _schema_intersection(parameters, parent_schema)
        selected = dict(self.actions).get(name)
        if selected is not None:
            schema = _schema_intersection(schema, {"type": "object", "properties": {
                "action": {"enum": list(selected)}}})
        if not isinstance(schema, dict):
            raise ValueError("Scoped child tool schema must be an object")
        return schema

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


class ChildSkills(SkillRepo):
    """Detached selected instruction bodies, never the parent's writable skill manager."""
    def __init__(self, parent: SkillRepo | None, selected: tuple[str, ...]) -> None:
        super().__init__(builtin=False)
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
        self._fixed = self._rows
        if sum(len(row.content.encode()) for row in self._rows.values()) > 1024 * 1024:
            raise ValueError('Delegated skill bodies exceed instruction capacity')

    @property
    def slugs(self) -> tuple[str, ...]:
        return tuple(slug for slug in self._selected if self._selection is None or slug in self._selection)

    def check_live(self, parent: SkillRepo | None) -> None:
        if not self.slugs:
            return
        if parent is not self._parent:
            raise ValueError('Parent skill catalog changed; delegate again')
        if parent is not None:
            if (not _native_catalog(parent)
                    and not set(self.slugs) <= {row.slug for row in parent.list_skills()}):
                raise ValueError("Delegated skill revoked from parent catalog")
            reader = _Reader(parent, frozenset(self.slugs), 262144, 512)
            for slug in self.slugs:
                if reader.load(slug, threading.Event()) != self._rows[slug]:
                    raise ValueError('Delegated skill changed or was revoked; delegate again')

    def list_skills(self) -> list[Skill]:
        return [replace(self._rows[slug], content='') for slug in self.slugs]

    def load_skill(self, slug: str) -> Skill | None:
        return self._rows.get(slug) if slug in self.slugs else None

    def register(self, skill: Skill | None = None, *, directory: Path | None = None,
                 name: str | None = None) -> SkillRepo:
        raise PermissionError('Delegated skill catalog is read-only')

    def unregister(self, name: str) -> None:
        raise PermissionError('Delegated skill catalog is read-only')

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
    _skills: Callable[[], SkillRepo | None] = field(repr=False, compare=False)

    def __init__(self, *, alive: Callable[[], bool], tools: Callable[[], Sequence[AgentTool]],
                 skills: Callable[[], SkillRepo | None]) -> None:
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
