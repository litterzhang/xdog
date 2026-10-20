"""Effective extension command bindings; built-in/user/editor keymap always wins."""

from __future__ import annotations

from collections import Counter
from typing import Any

from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.prompt_keymap import PromptKeymap, format_key, normalize_key


class ExtensionShortcuts:
    def __init__(self, rows: list[dict[str, Any]], keymap: PromptKeymap) -> None:
        self.active: list[dict[str, Any]] = []
        self.blocked: list[tuple[str, str]] = []
        overrides = {action: keys for action, keys in keymap.bindings if action.startswith("ext-")}
        reserved = {key: action for action, keys in keymap.bindings if not action.startswith("ext-") for key in keys}
        effective: list[dict[str, Any]] = []
        for row in rows:
            keys = overrides.get(row.get('action', row['command']), (normalize_key(row['key']),))
            if not keys:
                self.blocked.append((row["command"], "explicitly unbound by user keybindings"))
            effective.extend({**row, "key": key} for key in keys)
        counts = Counter(row["key"] for row in effective)
        for row in effective:
            key = row["key"]
            if key in reserved:
                self.blocked.append((row["command"], f"{format_key(key)} conflicts with {reserved[key]}"))
            elif counts[key] > 1:
                self.blocked.append((row["command"], f"{format_key(key)} conflicts between extensions; all disabled"))
            else:
                self.active.append(row)
        known = {row.get('action', row['command']) for row in rows}
        self.blocked.extend(
            (command, "configured binding has no active shortcut registration")
            for command in overrides
            if command not in known
        )

    def command(self, event: KeyEvent) -> str | None:
        if event.event_type != KeyEventType.PRESS:
            return None
        return next((row["command"] for row in self.active if event.matches(row["key"])), None)

    def help_text(self) -> str:
        rows = ["Extension shortcuts (idle prompt only; existing commands/permissions):"]
        rows.extend(f"  {format_key(row['key'])}: /{row['command']} — {row['description']}" for row in self.active)
        rows.extend(f"  /{command}: DISABLED — {reason}" for command, reason in self.blocked)
        return "\n".join(rows if len(rows) > 1 else [*rows, "  none active (activate trusted extensions explicitly)"])
