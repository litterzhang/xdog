"""Legacy keybinding records retained for programmatic compatibility.

Live Coding/Claw prompt bindings are validated and resolved by
``xdog.tui.prompt_keymap.PromptKeymap``. This older default list is not the
application's active shortcut configuration.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Keybinding:
    """A single keybinding definition."""

    key: str
    description: str
    action: str
    ctrl: bool = False
    alt: bool = False
    shift: bool = False


# Default keybindings for the interactive mode
DEFAULT_KEYBINDINGS: tuple[Keybinding, ...] = (
    Keybinding(key="Enter", description="Send message", action="send"),
    Keybinding(key="c", ctrl=True, description="Cancel / interrupt", action="cancel"),
    Keybinding(key="d", ctrl=True, description="Exit", action="exit"),
    Keybinding(key="l", ctrl=True, description="Clear screen", action="clear"),
    Keybinding(key="Up", description="Previous message", action="history_prev"),
    Keybinding(key="Down", description="Next message", action="history_next"),
    Keybinding(key="Tab", description="Autocomplete", action="autocomplete"),
    Keybinding(key="/", description="Slash command", action="slash_command"),
    Keybinding(key="r", ctrl=True, description="Retry last", action="retry"),
    Keybinding(key="z", ctrl=True, description="Undo", action="undo"),
)


class KeybindingManager:
    """Manages and resolves keybindings."""

    def __init__(self, bindings: tuple[Keybinding, ...] | None = None) -> None:
        self._bindings = list(bindings or DEFAULT_KEYBINDINGS)
        self._action_map: dict[str, Keybinding] = {b.action: b for b in self._bindings}

    def get_by_action(self, action: str) -> Keybinding | None:
        return self._action_map.get(action)

    def list_bindings(self) -> list[Keybinding]:
        return list(self._bindings)

    def add_binding(self, binding: Keybinding) -> None:
        self._bindings.append(binding)
        self._action_map[binding.action] = binding

    def to_help_text(self) -> str:
        """Produce a human-readable help string listing all keybindings."""
        lines: list[str] = ["Keybindings:", ""]
        for b in self._bindings:
            mod = ""
            if b.ctrl:
                mod += "Ctrl+"
            if b.alt:
                mod += "Alt+"
            if b.shift:
                mod += "Shift+"
            lines.append(f"  {mod}{b.key:<12s}  {b.description}")
        return "\n".join(lines)
