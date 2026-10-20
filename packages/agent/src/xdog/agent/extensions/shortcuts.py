"""Declarative command shortcut registration; hosts retain contextual key priority."""

from __future__ import annotations

import re
from typing import Any

_SPECIAL = {
    "enter",
    "tab",
    "escape",
    "backspace",
    "delete",
    "insert",
    "home",
    "end",
    "left",
    "right",
    "up",
    "down",
    "pageup",
    "pagedown",
    *(f"f{i}" for i in range(1, 25)),
}
_RESERVED = {"escape", "ctrl+c", "ctrl+d", "ctrl+l", "ctrl+z"}


def shortcut_key(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Invalid extension shortcut key")
    parts = [part.strip().lower() for part in value.split("+")]
    if not parts or any(not part for part in parts) or len(set(parts)) != len(parts):
        raise ValueError("Invalid extension shortcut modifiers")
    modifiers = {part for part in parts if part in {"ctrl", "alt", "shift"}}
    keys = [part for part in parts if part not in modifiers]
    if len(keys) != 1:
        raise ValueError("Extension shortcut must have one key")
    key = keys[0]
    if key not in _SPECIAL and not (len(key) == 1 and key.isascii() and key.isalnum()):
        raise ValueError("Unsupported extension shortcut key")
    if (
        len(key) == 1
        and not modifiers.intersection({"ctrl", "alt"})
        or not modifiers
        and not re.fullmatch(r"f(?:[1-9]|1[0-9]|2[0-4])", key)
    ):
        raise ValueError("Extension shortcuts cannot consume typing or unmodified dialog/editor keys")
    canonical = "+".join([*(part for part in ("ctrl", "alt", "shift") if part in modifiers), key])
    if canonical in _RESERVED:
        raise ValueError("Cancellation, exit, suspension and redraw keys are reserved")
    return canonical


def registrations(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("Invalid extension shortcut registration capacity")
    result = []
    names, keys = set(), set()
    for row in value:
        if (
            not isinstance(row, dict)
            or set(row) != {"name", "key", "description"}
            or not isinstance(row["name"], str)
            or not re.fullmatch("[A-Za-z][A-Za-z0-9_]{0,31}", row["name"])
            or not isinstance(row["description"], str)
            or len(row["description"].encode()) > 1000
        ):
            raise ValueError("Invalid extension shortcut registration")
        key = shortcut_key(row["key"])
        if key in keys or row["name"].lower() in names:
            raise ValueError("Duplicate extension shortcut key/name")
        keys.add(key)
        names.add(row["name"].lower())
        result.append({"name": row["name"], "key": key, "description": row["description"]})
    return result


def snapshot_rows(value: Any, commands: dict[str, str], active: set[str], *,
                  owners: dict[str, str] | None = None) -> list[dict[str, str]]:
    from xdog.agent.extensions.commands import owner_for
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError("Invalid extension shortcut snapshot capacity")
    result = []
    seen = set()
    for row in value:
        if (
            not isinstance(row, dict)
            or set(row) not in ({"extension", "command", "key", "description"},
                                {"extension", "command", "key", "description", "action"})
            or not isinstance(row["extension"], str)
            or row["extension"] not in active
            or not isinstance(row["command"], str)
            or row["command"] not in commands
            or (owner_for(row['command'], owners or {}) or '').lower() != row['extension'].lower()
            or ('action' in row and (not isinstance(row['action'], str)
                or (owner_for(row['action'], {}) or '').lower() != row['extension'].lower()))
            or row["command"] in seen
            or not isinstance(row["description"], str)
            or len(row["description"].encode()) > 1000
        ):
            raise ValueError("Invalid extension shortcut snapshot")
        key = shortcut_key(row["key"])
        seen.add(row["command"])
        result.append({**row, "key": key})
    return result
