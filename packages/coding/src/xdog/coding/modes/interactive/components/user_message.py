"""Coding style adapter for shared user messages."""

from __future__ import annotations

from xdog.coding.modes.interactive.theme import Theme
from xdog.tui.components.messages import UserMessage


class UserMessageComponent(UserMessage):
    def __init__(self, text: str, theme: Theme) -> None:
        super().__init__(text, theme.markdown, theme.user_default_text)
