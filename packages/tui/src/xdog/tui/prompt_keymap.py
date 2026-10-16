"""Validated, immutable keymaps for live prompt editors and their host actions."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xdog.tui.keys import KeyEvent

# Contextual dialog navigation and confirmation are deliberately not configurable.
# Escape and Ctrl+L remain unconditional denial/recovery routes in the TUI.
_DEFAULTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("send", ("enter",)),
    ("newline", ("ctrl+enter", "alt+enter", "shift+enter", "ctrl+alt+enter", "ctrl+shift+enter",
                 "alt+shift+enter", "ctrl+alt+shift+enter")),
    ("complete", ("tab",)),
    ("left", ("left",)), ("right", ("right",)),
    ("home", ("home",)), ("end", ("end",)),
    ("previous", ("up",)), ("next", ("down",)),
    ("backspace", ("backspace",)), ("delete", ("delete",)),
    ("start", ("ctrl+a",)), ("finish", ("ctrl+e",)),
    ("kill_end", ("ctrl+k",)), ("kill_start", ("ctrl+u",)),
    ("yank", ("ctrl+y",)), ("yank_pop", ("alt+y",)),
    ("undo", ("alt+z",)), ("redo", ("alt+shift+z",)),
    ("interrupt", ("ctrl+c",)), ("exit", ("ctrl+d",)),
    ("cancel", ("escape",)), ("redraw", ("ctrl+l",)),
    ("suspend", ("ctrl+z",)), ("details", ("ctrl+o",)),
    ("external_editor", ("ctrl+g",)), ("bindings", ("f1",)),
    ("save_draft", ("ctrl+alt+s",)),
    ("paste_clipboard", ("ctrl+v",)), ("copy", ("alt+c",)),
    ("sessions", ("alt+r",)), ("tree", ()),
    ("transcript", ("alt+h",)), ("fullscreen", ()),
    ("steer", ("alt+s",)), ("follow_up", ("alt+f",)), ("queue", ("ctrl+q",)),
)
_SPECIAL = frozenset({
    "enter", "tab", "escape", "backspace", "delete", "insert", "home", "end",
    "left", "right", "up", "down", "pageup", "pagedown", *(f"f{i}" for i in range(1, 25)),
})


def normalize_key(descriptor: str) -> str:
    parts = [part.strip().lower() for part in descriptor.split("+")]
    if not parts or any(not part for part in parts) or len(set(parts)) != len(parts):
        raise ValueError(f"invalid key descriptor: {descriptor!r}")
    modifiers = {part for part in parts if part in {"ctrl", "alt", "shift"}}
    keys = [part for part in parts if part not in modifiers]
    if len(keys) != 1:
        raise ValueError(f"expected one key: {descriptor!r}")
    key = keys[0]
    if key not in _SPECIAL and not (len(key) == 1 and key.isascii() and key.isalnum()):
        raise ValueError(f"unsupported key: {descriptor!r}")
    if len(key) == 1 and not modifiers.intersection({"ctrl", "alt"}):
        raise ValueError("printable shortcuts require Ctrl or Alt so typing remains available")
    return "+".join([*(part for part in ("ctrl", "alt", "shift") if part in modifiers), key])


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate action: {key}")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class PromptKeymap:
    bindings: tuple[tuple[str, tuple[str, ...]], ...] = _DEFAULTS
    error: str = ""

    @classmethod
    def from_overrides(cls, overrides: dict[str, Any]) -> PromptKeymap:
        merged = dict(_DEFAULTS)
        extension_actions: set[str] = set()
        for action, raw in overrides.items():
            extension = bool(re.fullmatch(r"ext-[A-Za-z][A-Za-z0-9_]{0,31}-[A-Za-z][A-Za-z0-9_]{0,31}", action))
            if action not in merged and not extension:
                raise ValueError(f"unknown action: {action}")
            if extension:
                normalized = action.lower()
                if normalized in extension_actions:
                    raise ValueError("Duplicate case-insensitive extension command binding")
                extension_actions.add(normalized)

            if action in {"cancel", "redraw"}:
                raise ValueError(f"{action} is reserved for contextual cancellation and recovery")
            values = [raw] if isinstance(raw, str) else raw
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise ValueError(f"{action} must be a key string or list of key strings")
            keys = tuple(normalize_key(item) for item in values)
            if extension:
                for key in keys:
                    if (key in {"escape", "ctrl+c", "ctrl+d", "ctrl+l", "ctrl+z"}
                            or "+" not in key and not re.fullmatch(r"f(?:[1-9]|1[0-9]|2[0-4])", key)):
                        raise ValueError("Extension bindings cannot take cancellation/recovery "
                                         "or unmodified editor keys")
            merged[action.lower() if extension else action] = keys
        owners: dict[str, str] = {}
        for action, keys in merged.items():
            for key in keys:
                if key in owners:
                    raise ValueError(f"{key} conflicts between {owners[key]} and {action}")
                owners[key] = action
        return cls(tuple(merged.items()))

    @classmethod
    def load(cls, path: Path) -> PromptKeymap:
        try:
            with path.open("rb") as stream:
                data = stream.read(65_537)
            if len(data) > 65_536:
                raise ValueError("keymap exceeds 64 KiB")
            raw = json.loads(data, object_pairs_hook=_unique_object)
            if not isinstance(raw, dict):
                raise ValueError("keymap must be an action-to-keys JSON object")
            return cls.from_overrides(raw)
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError, UnicodeError) as exc:
            return cls(error=f"Cannot load keybindings ({path}): {exc}; using defaults")

    def resolve(self, event: KeyEvent) -> str | None:
        for action, keys in self.bindings:
            if any(event.matches(key) for key in keys):
                return action
        return None

    def label(self, action: str) -> str:
        keys = dict(self.bindings).get(action, ())
        return format_key(keys[0]) if keys else "unbound"

    def help_text(self) -> str:
        rows = ["Active keybindings (prompt actions):"]
        for action, keys in self.bindings:
            rows.append(f"  {', '.join(format_key(key) for key in keys) or 'unbound'}: {action}")
        rows.extend(("", "Escape and Ctrl+L are reserved. Dialog navigation/approval keys are unchanged.",
                     "External editor: VISUAL, then EDITOR, then vi. /editor also opens it."))
        return "\n".join(rows)


def format_key(descriptor: str) -> str:
    return "+".join("Esc" if part == "escape" else part.upper() if len(part) == 1 else part.title()
                    for part in descriptor.split("+"))


# Canonical editor events reuse its grapheme/history/undo machinery. A removed
# binding no longer falls through to its old hard-coded action.
EDITOR_EVENTS: dict[str, KeyEvent] = {
    action: KeyEvent(
        key=keys[0].split("+")[-1], ctrl="ctrl" in keys[0].split("+"),
        alt="alt" in keys[0].split("+"), shift="shift" in keys[0].split("+"),
    ) for action, keys in _DEFAULTS if action not in {
        "redraw", "suspend", "details", "external_editor", "bindings", "steer", "follow_up", "queue",
        "save_draft",
        "sessions", "tree", "paste_clipboard", "transcript", "fullscreen", "copy",
    }
}
