"""Coding style adapter for independently retained assistant messages."""

from __future__ import annotations

from xdog.coding.modes.interactive.theme import Theme
from xdog.tui.components.messages import AssistantMessages


class AssistantMessageComponent(AssistantMessages):
    def __init__(self, text: str, theme: Theme, *, thinking: str = "") -> None:
        super().__init__(text, thinking, theme.markdown, theme.dim, expanded=True)
