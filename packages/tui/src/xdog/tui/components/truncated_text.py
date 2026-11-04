"""TruncatedText component -- displays text truncated to fit width.

Ported from TypeScript truncated-text.ts.
"""

from __future__ import annotations

from xdog.tui.tui import Component
from xdog.tui.utils import string_width, truncate_to_width


class TruncatedText(Component):
    """Text component that truncates to fit viewport width."""

    def __init__(self, text: str = "", padding_x: int = 0, padding_y: int = 0) -> None:
        self._text = text
        self._padding_x = padding_x
        self._padding_y = padding_y

    def set_text(self, text: str) -> None:
        self._text = text

    def invalidate(self) -> None:
        pass

    def render(self, width: int) -> list[str]:
        result: list[str] = []
        empty_line = " " * width

        for _ in range(self._padding_y):
            result.append(empty_line)

        available = max(1, width - self._padding_x * 2)
        single_line = self._text.split("\n", 1)[0]
        display = truncate_to_width(single_line, available)
        left = " " * self._padding_x
        line = left + display
        vis = string_width(line)
        result.append(line + " " * max(0, width - vis))

        for _ in range(self._padding_y):
            result.append(empty_line)

        return result
