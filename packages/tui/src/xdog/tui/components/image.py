"""Image component -- terminal inline image display.

Uses the Kitty Graphics Protocol or iTerm2 Inline Images Protocol
for real image rendering, with text fallback when no protocol is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from xdog.tui.terminal_image import (
    ImageRenderOptions,
    get_capabilities,
    get_image_dimensions,
    image_fallback,
    render_image,
)
from xdog.tui.tui import Component


@dataclass(frozen=True)
class ImageTheme:
    """Theme for the image component."""

    fallback_color: Callable[[str], str] = lambda s: s


@dataclass(frozen=True)
class ImageOptions:
    """Options for image rendering."""

    max_width_cells: int = 60
    max_height_cells: int | None = None
    filename: str | None = None
    image_id: int | None = None


class Image(Component):
    """Image component with Kitty/iTerm2 protocol support.

    Accepts raw image bytes or a file path. Falls back to text when
    no image protocol is available.
    """

    def __init__(
        self,
        data: bytes | None = None,
        *,
        path: str | None = None,
        alt: str = "image",
        theme: ImageTheme | None = None,
        options: ImageOptions | None = None,
    ) -> None:
        self._data = data
        self._path = path
        self._alt = alt
        self._theme = theme or ImageTheme()
        self._options = options or ImageOptions()
        self._image_id: int | None = self._options.image_id
        self._cached_lines: list[str] | None = None
        self._cached_width: int | None = None

    def get_image_id(self) -> int | None:
        """Return the Kitty image ID used by this image (if any)."""
        return self._image_id

    def invalidate(self) -> None:
        self._cached_lines = None
        self._cached_width = None

    def _load_data(self) -> bytes | None:
        """Load image data from path if not already loaded."""
        if self._data is not None:
            return self._data
        if self._path:
            try:
                return Path(self._path).read_bytes()
            except (OSError, IOError):
                return None
        return None

    def render(self, width: int) -> list[str]:
        if self._cached_lines is not None and self._cached_width == width:
            return self._cached_lines

        data = self._load_data()
        max_width = min(width - 2, self._options.max_width_cells)

        if data is None:
            fallback = image_fallback(self._alt, filename=self._options.filename)
            self._cached_lines = [self._theme.fallback_color(fallback)]
            self._cached_width = width
            return self._cached_lines

        caps = get_capabilities()

        if caps.images is not None:
            result = render_image(
                data,
                options=ImageRenderOptions(
                    max_width_cells=max_width,
                    max_height_cells=self._options.max_height_cells,
                    image_id=self._image_id,
                ),
            )

            if result is not None:
                if result.image_id is not None:
                    self._image_id = result.image_id

                lines: list[str] = []
                for _ in range(result.rows - 1):
                    lines.append("")
                move_up = f"\x1b[{result.rows - 1}A" if result.rows > 1 else ""
                lines.append(move_up + result.sequence)

                self._cached_lines = lines
                self._cached_width = width
                return lines

        # Fallback
        dims = get_image_dimensions(data) if data else None
        fallback = image_fallback(self._alt, dimensions=dims, filename=self._options.filename)
        self._cached_lines = [self._theme.fallback_color(fallback)]
        self._cached_width = width
        return self._cached_lines


# Backward compatibility
ImageComponent = Image
