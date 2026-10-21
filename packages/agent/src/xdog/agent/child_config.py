"""Validated per-child specialization controls; never a permission grant."""
from __future__ import annotations

import copy
import io
import json
import math
import re
import shlex
from dataclasses import dataclass, field, replace
from typing import Any


def child_tool_scopes(value: Any, tools: tuple[str, ...] | list[str] | None) -> dict[str, Any]:
    """Detached finite JSON data only; names select tools, never import code or grant authority."""
    remaining = 4096
    def check(item: Any, depth: int = 0) -> None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 16:
            raise ValueError("Child tool_scopes exceeds JSON structural bounds")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("Child tool_scopes requires JSON string keys")
            for child in item.values():
                check(child, depth + 1)
        elif type(item) is list:
            for child in item:
                check(child, depth + 1)
        elif type(item) not in (str, int, float, bool, type(None)) or (
                type(item) is float and not math.isfinite(item)):
            raise ValueError("Child tool_scopes must be finite JSON data")
    if (type(value) is not dict or len(value) > 128
            or any(type(scope) is not dict for scope in value.values())):
        raise ValueError("Child tool_scopes must map selected tools to JSON objects")
    check(value)
    if value and (tools is None or not set(value) <= set(tools)):
        raise ValueError("Child tool_scopes requires explicit tools containing every scoped tool")
    if len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8")) > 65536:
        raise ValueError("Child tool_scopes exceeds 64 KiB")
    return copy.deepcopy(value)


@dataclass(frozen=True, slots=True)
class ChildConfig:
    """Data-only selection; hosts validate actions against actual parent tool schemas.

    tool_actions maps multi-action tools to literal action values, e.g.
    {"filesystem": ["read"]}. It does not match shell commands or expand '*'.
    Bash has command, not action: select it through tools, omit its tool_actions
    entry. Neither omission nor access='inherit' grants additional authority.
    Historical profiles remain readable; fresh admission must capture live tools.
    """

    label: str = ""
    instructions: str = ""
    tools: tuple[str, ...] | None = None
    inherit_context: bool = False
    model: str | None = None
    thinking: str | None = None
    preset: str | None = None
    system_prompt: str = ""
    # None delegates catalog selection to the host; () deliberately selects no knowledge.
    skills: tuple[str, ...] | None = ()
    tool_actions: tuple[tuple[str, tuple[str, ...]], ...] = ()
    tool_scopes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "instructions": self.instructions,
                "tools": list(self.tools) if self.tools is not None else None,
                "inherit_context": self.inherit_context,
                "model": self.model, "thinking": self.thinking, "preset": self.preset,
                "system_prompt": self.system_prompt,
                "skills": list(self.skills) if self.skills is not None else None,
                "tool_actions": {name: list(actions) for name, actions in self.tool_actions},
                "tool_scopes": child_tool_scopes(self.tool_scopes, self.tools)}

    @classmethod
    def from_dict(cls, value: Any) -> ChildConfig:
        if not isinstance(value, dict) or set(value) - set(cls().to_dict()):
            raise ValueError("Unknown/invalid child configuration fields")
        merged = {**cls().to_dict(), **value}
        for name, limit in (("label", 120), ("instructions", 16000), ("system_prompt", 16000)):
            text = merged[name]
            if (not isinstance(text, str) or len(text.encode("utf-8")) > limit
                    or name == "label" and any(ord(char) < 32 or ord(char) == 127 for char in text)):
                raise ValueError(f"Invalid child {name}")
        if type(merged["inherit_context"]) is not bool or merged["inherit_context"]:
            raise ValueError("Child inherit_context must be false; parent history is never inherited")
        model, thinking = merged["model"], merged["thinking"]
        if model is not None and (not isinstance(model, str) or not model or len(model.encode("utf-8")) > 256
                                  or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in model)):
            raise ValueError("Invalid child model identifier")
        if thinking is not None and (not isinstance(thinking, str)
                                     or thinking not in {"off", "minimal", "low", "medium", "high", "xhigh"}):
            raise ValueError("Invalid child thinking level")
        preset = merged["preset"]
        if (preset is not None and (not isinstance(preset, str)
                                   or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", preset))):
            raise ValueError("Invalid child preset identifier")
        tools = merged["tools"]
        if tools is not None:
            if (not isinstance(tools, list) or len(tools) > 128
                    or any(not isinstance(tool, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", tool)
                           for tool in tools) or len(set(tools)) != len(tools)
                    or any(tool in {"agent", "agent_spawn", "agent_control", "update_plan"} for tool in tools)):
                raise ValueError("Child tools must be distinct non-reserved tool names (at most 128)")
        skills = merged["skills"]
        if skills is not None and (not isinstance(skills, list) or len(skills) > 128
                or any(not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", slug)
                       for slug in skills) or len(set(skills)) != len(skills)):
            raise ValueError("Child skills must be distinct skill slugs (at most 128), or null")
        actions = merged["tool_actions"]
        if not isinstance(actions, dict) or len(actions) > 128:
            raise ValueError("Child tool_actions must be a tool-name to action-array object")
        reserved = {"agent", "agent_spawn", "agent_control", "update_plan"}
        for name, selected in actions.items():
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", name)
                    or name in reserved or not isinstance(selected, list) or len(selected) > 128
                    or any(not isinstance(action, str) or not action or len(action.encode("utf-8")) > 128
                           or any(ord(char) < 32 or ord(char) == 127 for char in action) for action in selected)
                    or len(set(selected)) != len(selected)):
                raise ValueError("Child tool_actions requires non-reserved tool names and distinct action strings")
        if tools is not None and set(actions) - set(tools):
            raise ValueError("Child tool_actions cannot name tools outside the selected tool subset")
        return cls(label=merged["label"], instructions=merged["instructions"],
                   tools=tuple(tools) if tools is not None else None,
                   inherit_context=False, model=model, thinking=thinking, preset=preset,
                   system_prompt=merged["system_prompt"], skills=tuple(skills) if skills is not None else None,
                   tool_actions=tuple((name, tuple(selected)) for name, selected in sorted(actions.items())),
                   tool_scopes=child_tool_scopes(merged["tool_scopes"], tools))

    @classmethod
    def from_record(cls, value: Any) -> ChildConfig:
        """Historical inheritance/budgets are inert, never admission controls."""
        if not isinstance(value, dict):
            raise ValueError("Invalid persisted child configuration")
        historical = value.get("inherit_context", False)
        if type(historical) is not bool:
            raise ValueError("Child inherit_context must be boolean")
        config = cls.from_dict({**{key: item for key, item in value.items()
                                  if key not in {"timeout", "max_model_calls", "max_tokens"}},
                                "inherit_context": False})
        return replace(config, inherit_context=historical)



@dataclass(frozen=True, slots=True)
class ChildPreset:
    name: str
    description: str
    config: ChildConfig

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "config": self.config.to_dict()}


@dataclass(frozen=True, slots=True, init=False)
class ChildPresets:
    """Bounded immutable data-only templates. Access/approval/activation are NOT preset fields."""
    rows: tuple[ChildPreset, ...]
    def __init__(self, value: Any = None) -> None:
        if value is None:
            value = {}
        if not isinstance(value, dict) or len(value) > 32:
            raise ValueError("child_presets must be at most 32 named descriptors")
        try:
            if len(json.dumps(value).encode("utf-8")) > 256 * 1024:
                raise ValueError("Child preset catalog exceeds 256 KiB")
        except (TypeError, OverflowError) as exc:
            raise ValueError("Child preset catalog must be JSON data") from exc
        rows = []
        for name, descriptor in value.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
                raise ValueError("Invalid child preset name")
            if (not isinstance(descriptor, dict) or set(descriptor) - {"description", "config"}
                    or not isinstance(descriptor.get("description", ""), str)
                    or len(descriptor.get("description", "").encode("utf-8")) > 1000):
                raise ValueError("Invalid child preset descriptor")
            config = ChildConfig.from_dict(descriptor.get("config", {}))
            if config.preset is not None:
                raise ValueError("Child presets cannot chain other presets")
            if not config.label:
                config = ChildConfig.from_dict({**config.to_dict(), "label": name})
            rows.append(ChildPreset(name, descriptor.get("description", ""), config))
        normalized = {row.name: {"description": row.description, "config": row.config.to_dict()} for row in rows}
        if len(json.dumps(normalized).encode("utf-8")) > 256 * 1024:
            raise ValueError("Expanded child preset catalog exceeds 256 KiB")
        object.__setattr__(self, "rows", tuple(sorted(rows, key=lambda row: row.name)))

    @classmethod
    def from_snapshot(cls, rows: Any) -> ChildPresets:
        if not isinstance(rows, (list, tuple)) or len(rows) > 32:
            raise ValueError("Invalid child preset snapshot")
        values: dict[str, Any] = {}
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"name", "description", "config"}
                    or not isinstance(row["name"], str) or row["name"] in values):
                raise ValueError("Invalid/duplicate child preset identity")
            values[row["name"]] = {"description": row["description"],
                                   "config": {**ChildConfig.from_record(row["config"]).to_dict(),
                                              "inherit_context": False}}
        return cls(values)

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        return tuple(row.to_dict() for row in self.rows)

    def resolve(self, value: Any) -> ChildConfig:
        config = ChildConfig.from_dict(value)
        if config.preset is None:
            return config
        row = next((row for row in self.rows if row.name == config.preset), None)
        if row is None:
            raise ValueError("Unknown child preset; inspect /agents presets before starting work")
        return ChildConfig.from_dict({**row.config.to_dict(), **value})


def parse_child_start(text: str, presets: ChildPresets | None = None) -> tuple[str, str, ChildConfig]:
    task, access, value = parse_child_options(text)
    return task, access, (presets or ChildPresets()).resolve(value)


def parse_child_options(text: str) -> tuple[str, str, dict[str, Any]]:
    """Explicit flags before TASK; `--` preserves tasks beginning with flags.

    Keep plain task text unchanged (including quotes/newlines) when no flags are
    present. Flag values use shell quoting, but no shell, file or code is evaluated.
    """
    remaining = text.lstrip()
    value: dict[str, Any] = {}
    access = "read-only"
    seen: set[str] = set()
    flags = {"--label": "label", "--instructions": "instructions", "--tools": "tools",
             "--model": "model", "--thinking": "thinking", "--preset": "preset",
             "--system-prompt": "system_prompt", "--skills": "skills"}
    while remaining.startswith("--"):
        # shlex lexer's stream position avoids rewriting the task suffix.
        source = io.StringIO(remaining)
        lexer = shlex.shlex(source, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        flag = lexer.get_token()
        assert isinstance(flag, str)
        if flag == "--":
            remaining = remaining[source.tell():].lstrip()
            break
        if flag in seen:
            raise ValueError("Duplicate child option: " + flag)
        seen.add(flag)
        if flag == "--inherit":
            access = "inherit"
        elif flag == "--fresh":
            value["inherit_context"] = False
        elif flag in flags:
            argument = lexer.get_token()
            if argument is None or argument == lexer.eof:
                raise ValueError("Missing value for " + flag)
            key = flags[flag]
            try:
                value[key] = ([] if key in {"tools", "skills"} and argument == "none" else argument.split(",")
                              if key in {"tools", "skills"} else argument)
            except ValueError as exc:
                raise ValueError("Invalid value for " + flag) from exc
        else:
            raise ValueError("Unknown child option: " + str(flag))
        remaining = remaining[source.tell():].lstrip()
    if not remaining.strip() or len(remaining.encode("utf-8")) > 16000:
        raise ValueError("Child start requires a task of at most 16 KiB")
    ChildConfig.from_dict(value)
    return remaining, access, value
