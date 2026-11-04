"""Loader component -- animated terminal spinner.

Ported from TypeScript loader.ts to use the string-based rendering model.
"""

from __future__ import annotations

import threading
from typing import Callable

from xdog.tui.components.text import Text


class Loader(Text):
    """Loader component that updates with spinning animation.

    Ported from TypeScript Loader class.
    """

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(
        self,
        tui: object,
        spinner_fn: Callable[[str], str],
        message_fn: Callable[[str], str],
        message: str = "Loading...",
    ) -> None:
        super().__init__("", 1, 0)
        self._tui = tui
        self._spinner_fn = spinner_fn
        self._message_fn = message_fn
        self._message = message
        self._frame = 0
        self._timer: threading.Timer | None = None
        self.start()

    def render(self, width: int) -> list[str]:
        result = super().render(width)
        if result is not None:
            return ["", *result]
        return None

    def start(self) -> None:
        """Start the spinner animation."""
        self._update_display()
        self._schedule_next()

    def stop(self) -> None:
        """Stop the spinner animation."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def set_message(self, message: str) -> None:
        """Update the loading message."""
        self._message = message
        self._update_display()

    def _schedule_next(self) -> None:
        """Schedule next frame update."""
        self._timer = threading.Timer(0.08, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self) -> None:
        """Advance to next frame and schedule update."""
        self._frame = (self._frame + 1) % len(self.FRAMES)
        self._update_display()
        self._schedule_next()

    def _update_display(self) -> None:
        """Update the displayed text with current frame."""
        frame = self.FRAMES[self._frame]
        self.set_text(
            f"{self._spinner_fn(frame)} {self._message_fn(self._message)}"
        )
        if self._tui and hasattr(self._tui, "request_render"):
            self._tui.request_render()

    def preferred_height(self, width: int) -> int:
        return max(1, super().preferred_height(width) + 1)
