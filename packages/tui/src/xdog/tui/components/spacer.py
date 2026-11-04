"""Spacer component -- renders empty lines.

Ported from TypeScript spacer.ts to use the string-based rendering model.
"""

from __future__ import annotations

from xdog.tui.tui import Component


class Spacer(Component):
    """Spacer component that renders empty lines.

    Ported from TypeScript Spacer class.
    """

    def __init__(self, lines: int = 1) -> None:
        super().__init__()
        self._lines = lines

    def set_lines(self, lines: int) -> None:
        """Set the number of empty lines."""
        self._lines = lines

    def invalidate(self) -> None:
        pass

    def render(self, width: int) -> list[str]:
        return [""] * self._lines

    def preferred_height(self, width: int) -> int:
        return self._lines
