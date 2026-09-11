"""Standalone, width-bounded status presentation without application policy."""
from __future__ import annotations

from collections.abc import Callable

from xdog.tui.components.text import Text
from xdog.tui.utils import sanitize_terminal_text, string_width, truncate_to_width


class StatusLine(Text):
    """Render plain status or prioritized activity/context/model fields."""

    def __init__(self, dim: Callable[[str], str], *, structured: bool = False) -> None:
        super().__init__("", 0, 0)
        self._dim = dim
        self._structured = structured
        self._activity = "ready"
        self._queued = 0
        self._model = ""
        self._context = ""

    def set_queue_count(self, count: int) -> None:
        self._queued = count

    def set_activity(self, activity: str) -> None:
        if self._activity != activity:
            self._activity = activity
            self.invalidate()

    def set_fields(self, *, activity: str, model: str = "", context: str = "", queued: int = 0) -> None:
        self._structured = True
        self._activity = " ".join(sanitize_terminal_text(activity).split())
        self._model = " ".join(sanitize_terminal_text(model).split())
        self._context = " ".join(sanitize_terminal_text(context).split())
        self._queued = queued
        self.invalidate()

    def render(self, width: int) -> list[str]:
        """Keep activity, queue and context before optional model metadata."""
        width = max(1, width)
        if not self._structured:
            text = " ".join(sanitize_terminal_text(self.text).split())
            return [self._dim(truncate_to_width(text, width, "…"))]
        # Reserve capacity and a recognizable model name before truncating long activity.
        context_width = string_width(self._context) + 3 if self._context else 0
        model_budget = min(string_width(self._model), max(0, width - context_width - 15))
        activity_budget = max(5, width - context_width - (model_budget + 3 if model_budget else 0))
        left = truncate_to_width(self._activity, activity_budget, "…")
        fields = ([f"queued {self._queued}"] if self._queued else []) + ([self._context] if self._context else [])
        for field in fields:
            candidate = left + " | " + field
            if string_width(candidate) <= width:
                left = candidate
        available = width - string_width(left) - 3
        if self._model and available >= 4:
            model = truncate_to_width(self._model, available, "…")
            left += " " * max(3, width - string_width(left) - string_width(model)) + self._dim(model)
        return [truncate_to_width(left, width, "…")]
