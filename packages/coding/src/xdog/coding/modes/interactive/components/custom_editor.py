"""Coding adapter for the shared prompt editor."""

from __future__ import annotations

from xdog.coding.core.slash_commands import list_commands
from xdog.coding.modes.interactive.theme import Theme
from xdog.tui.components.input_panel import InputPanel, SlashSelectList

_MAX_INPUT_ROWS = 8


class CustomEditorComponent(InputPanel):
    """Prompt editor configured with coding's theme and slash commands."""

    def __init__(self, theme: Theme) -> None:
        super().__init__(theme, command_provider=lambda: {
            **list_commands(), "transcript": "Search/select full transcript",
            "fullscreen": "Toggle fullscreen transcript viewer",
            "copy": "Copy response/selected source", "export": "Save private transcript snapshot",
            "share": "Review local shareable HTML (no upload)", "details": "Open details panel: /details [close]",
            "status": "Show full session and runtime status",
            "attach": "Attach an image file", "attachments": "Inspect/remove/preview images",
            "paste": "Read clipboard", "diff": "Read-only repository-wide change review",
            "queue": "Inspect/edit/remove/reorder queued inputs", "steer": "Send at next checkpoint",
            "follow-up": "Send after active tool continuation",
            "keys": "Show active keybindings", "editor": "Open external prompt editor",
        }, max_rows=_MAX_INPUT_ROWS)


__all__ = ["CustomEditorComponent", "SlashSelectList", "Theme", "list_commands"]
