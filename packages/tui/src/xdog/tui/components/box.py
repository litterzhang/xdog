"""Box component -- a container that applies padding and background to children.

Ported from TypeScript box.ts to use the string-based rendering model.
"""

from __future__ import annotations

from typing import Callable

from xdog.tui.tui import Component
from xdog.tui.utils import apply_background_to_line, string_width


class Box(Component):
    """Box component -- applies padding and background to all children.

    Ported from TypeScript Box class.
    """

    def __init__(
        self,
        padding_x: int = 1,
        padding_y: int = 1,
        bg_fn: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__()
        self.children: list[Component] = []
        self._padding_x = padding_x
        self._padding_y = padding_y
        self._bg_fn = bg_fn

    def add_child(self, component: Component) -> None:
        self.children.append(component)

    def remove_child(self, component: Component) -> None:
        if component in self.children:
            self.children.remove(component)

    def clear(self) -> None:
        self.children.clear()

    def set_bg_fn(self, bg_fn: Callable[[str], str] | None = None) -> None:
        """Set the background color function."""
        self._bg_fn = bg_fn

    def invalidate(self) -> None:
        for child in self.children:
            child.invalidate()

    def render(self, width: int) -> list[str]:
        return self._render_lines(width)

    def _render_lines(self, width: int) -> list[str]:
        """Core render logic returning string lines."""
        if not self.children:
            return []

        content_width = max(1, width - self._padding_x * 2)
        left_pad = " " * self._padding_x

        # Render all children
        child_lines: list[str] = []
        for child in self.children:
            rendered = child.render(content_width)
            if rendered is not None:
                for line in rendered:
                    child_lines.append(left_pad + line)

        if not child_lines:
            return []

        # Apply background and padding
        result: list[str] = []

        # Top padding
        for _ in range(self._padding_y):
            result.append(self._apply_bg("", width))

        # Content
        for line in child_lines:
            result.append(self._apply_bg(line, width))

        # Bottom padding
        for _ in range(self._padding_y):
            result.append(self._apply_bg("", width))

        return result

    def _apply_bg(self, line: str, width: int) -> str:
        """Apply background and pad to width."""
        vis_len = string_width(line)
        pad_needed = max(0, width - vis_len)
        padded = line + " " * pad_needed

        if self._bg_fn:
            return apply_background_to_line(padded, width, self._bg_fn)
        return padded

    def preferred_height(self, width: int) -> int:
        lines = self._render_lines(width)
        return max(1, len(lines))
