"""Text component -- displays multi-line text with word wrapping.

Ported from TypeScript text.ts to use the string-based rendering model.
"""

from __future__ import annotations

from typing import Callable

from xdog.tui.tui import Component
from xdog.tui.utils import (
    apply_background_to_line,
    string_width,
    wrap_text_with_ansi,
)


class Text(Component):
    """Text component that renders to ANSI-styled string lines.

    Ported from TypeScript Text class.
    """

    def __init__(
        self,
        text: str = "",
        padding_x: int = 1,
        padding_y: int = 1,
        bg_fn: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__()
        self._text = text
        self._padding_x = padding_x
        self._padding_y = padding_y
        self._bg_fn = bg_fn
        # Cache
        self._cached_text: str | None = None
        self._cached_width: int | None = None
        self._cached_lines: list[str] | None = None

    def set_text(self, text: str) -> None:
        """Set the text content and invalidate cache."""
        self._text = text
        self.invalidate()

    def set_bg_fn(self, bg_fn: Callable[[str], str] | None = None) -> None:
        """Set the background color function."""
        self._bg_fn = bg_fn
        self.invalidate()

    def invalidate(self) -> None:
        self._cached_text = None
        self._cached_width = None
        self._cached_lines = None

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        self.set_text(value)

    def render(self, width: int) -> list[str]:
        return self._render_lines(width)

    def _render_lines(self, width: int) -> list[str]:
        """Core render logic returning string lines."""
        # Check cache
        if (
            self._cached_lines is not None
            and self._cached_text == self._text
            and self._cached_width == width
        ):
            return self._cached_lines

        # Don't render anything if there's no actual text
        if not self._text or self._text.strip() == "":
            result: list[str] = []
            self._cached_text = self._text
            self._cached_width = width
            self._cached_lines = result
            return result

        # Replace tabs with 3 spaces
        normalized_text = self._text.replace("\t", "   ")

        # Calculate content width (subtract left/right margins)
        content_width = max(1, width - self._padding_x * 2)

        # Wrap text (preserves ANSI codes but does NOT pad)
        wrapped_lines = wrap_text_with_ansi(normalized_text, content_width)

        # Add margins and background to each line
        left_margin = " " * self._padding_x
        right_margin = " " * self._padding_x
        content_lines: list[str] = []

        for line in wrapped_lines:
            line_with_margins = left_margin + line + right_margin

            if self._bg_fn:
                content_lines.append(
                    apply_background_to_line(line_with_margins, width, self._bg_fn)
                )
            else:
                vis_len = string_width(line_with_margins)
                padding_needed = max(0, width - vis_len)
                content_lines.append(line_with_margins + " " * padding_needed)

        # Add top/bottom padding (empty lines)
        empty_line = " " * width
        empty_lines: list[str] = []
        for _ in range(self._padding_y):
            if self._bg_fn:
                empty_lines.append(
                    apply_background_to_line(empty_line, width, self._bg_fn)
                )
            else:
                empty_lines.append(empty_line)

        result = [*empty_lines, *content_lines, *empty_lines]

        # Update cache
        self._cached_text = self._text
        self._cached_width = width
        self._cached_lines = result

        return result if result else [""]

    def preferred_height(self, width: int) -> int:
        """Return the number of lines this text renders at the given width."""
        lines = self._render_lines(width)
        return max(1, len(lines))
