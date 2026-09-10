"""Footer component for the interactive TUI."""

from __future__ import annotations

from xdog.coding.modes.interactive.theme import Theme, format_tokens
from xdog.tui.components.text import Text
from xdog.tui.utils import truncate_to_width


class FooterComponent(Text):
    """Bottom status bar showing model, session, tokens, and working directory."""

    def __init__(self, theme: Theme) -> None:
        super().__init__("", 0, 0)
        self._theme = theme
        self._activity = "ready"
        self._metadata: tuple[str, ...] = ()
        self._queued = 0

    def set_queue_count(self, count: int) -> None:
        self._queued = count

    def set_activity(self, activity: str) -> None:
        """Set the single activity label without rebuilding metadata."""
        if activity == self._activity:
            return
        self._activity = activity
        self.invalidate()

    def update(
        self,
        *,
        model: str = "unknown",
        session_id: str = "",
        message_count: int = 0,
        thinking: str = "off",
        permission_mode: str = "ask",
        working_dir: str = "",
        context_tokens: int = 0,
        max_context: int = 200_000,
    ) -> None:
        """Update footer content with current session state."""
        parts: list[str] = []

        if model:
            parts.append(model)
        if thinking and thinking != "off":
            parts.append(f"thinking:{thinking}")
        if permission_mode:
            parts.append(f"permissions:{permission_mode}")
        if session_id:
            parts.append(f"session:{session_id[:8]}")
        if message_count > 0:
            parts.append(f"msgs:{message_count}")
        if context_tokens > 0 and max_context > 0:
            pct = min(100.0, context_tokens / max_context * 100)
            parts.append(f"ctx:{pct:.0f}%/{format_tokens(max_context)}")
        if working_dir:
            # Show last two path components
            segments = working_dir.rstrip("/").split("/")
            short = "/".join(segments[-2:]) if len(segments) > 2 else working_dir
            parts.append(short)

        metadata = tuple(parts)
        if metadata == self._metadata:
            return
        self._metadata = metadata
        self.invalidate()

    def render(self, width: int) -> list[str]:
        """Render exactly one activity/metadata row, elided to terminal width."""
        queue = (f"queued {self._queued}",) if self._queued else ()
        content = " | ".join((self._activity, *queue, *self._metadata))
        return [self._theme.dim(truncate_to_width(content, max(1, width), "…"))]
