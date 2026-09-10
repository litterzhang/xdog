"""Coding adapter for the shared prompt editor."""

from __future__ import annotations

from xdog.coding.core.slash_commands import list_commands
from xdog.coding.modes.interactive.theme import Theme
from xdog.tui.components.prompt_editor import PromptEditor, SlashSelectList

_MAX_INPUT_ROWS = 8


class CustomEditorComponent(PromptEditor):
    """Prompt editor configured with coding's theme and slash commands."""

    def __init__(self, theme: Theme) -> None:
        super().__init__(theme, command_provider=list_commands, max_rows=_MAX_INPUT_ROWS)


__all__ = ["CustomEditorComponent", "SlashSelectList", "Theme", "list_commands"]
