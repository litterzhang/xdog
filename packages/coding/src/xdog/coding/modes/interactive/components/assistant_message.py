"""Assistant message component for the interactive TUI."""

from __future__ import annotations

from xdog.coding.modes.interactive.theme import Theme
from xdog.tui.components.markdown import Markdown
from xdog.tui.components.spacer import Spacer
from xdog.tui.components.text import Text
from xdog.tui.tui import Container
from xdog.tui.utils import sanitize_terminal_text


class AssistantMessageComponent(Container):
    """Renders a streaming or complete assistant message."""

    def __init__(self, text: str, theme: Theme, *, thinking: str = "") -> None:
        super().__init__()
        self._theme = theme
        self._thinking_content = sanitize_terminal_text(thinking)
        self._expanded = True
        self._thinking = Text("", 1, 0)
        self._body = Markdown(sanitize_terminal_text(text), 1, 0, theme.markdown)
        self.add_child(Spacer(1))
        self.add_child(self._thinking)
        self.add_child(self._body)
        self.set_content(text, thinking=thinking)

    @property
    def detail_title(self) -> str:
        """Stable title used by bounded detail providers."""
        return "reasoning"

    @property
    def detail_body(self) -> str:
        """Return the complete retained reasoning content."""
        return self._thinking_content

    def set_text(self, text: str) -> None:
        """Update response text while preserving current reasoning text."""
        self._body.set_text(sanitize_terminal_text(text))

    def set_content(self, text: str, *, thinking: str = "") -> None:
        """Update retained reasoning and the final response."""
        self._thinking_content = sanitize_terminal_text(thinking)
        self._render_thinking()
        self._body.set_text(sanitize_terminal_text(text))

    def set_expanded(self, expanded: bool) -> None:
        """Show full reasoning or its compact retained placeholder."""
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self._render_thinking()

    def _render_thinking(self) -> None:
        thinking = self._thinking_content.strip()
        if not thinking:
            rendered = ""
        elif self._expanded:
            rendered = f"Thinking\n{self._thinking_content}"
        else:
            rendered = "Thinking (Ctrl+O: details below input · ←/→ select entry)"
        self._thinking.set_text(self._theme.dim(rendered))
