"""Footer component for the interactive TUI."""

from __future__ import annotations

from xdog.coding.modes.interactive.theme import Theme, format_tokens
from xdog.tui.components.status_line import StatusLine


class FooterComponent(StatusLine):
    """Bottom status bar showing model, session, tokens, and working directory."""

    def __init__(self, theme: Theme) -> None:
        super().__init__(theme.dim, structured=True)
        self._metadata: tuple[str, ...] = ()

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
        self._model = model.rsplit("/", 1)[-1]
        self._context = ""
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
        if max_context > 0:
            pct = min(100.0, context_tokens / max_context * 100)
            self._context = f"ctx:{format_tokens(context_tokens)}/{format_tokens(max_context)}"
            parts.append(f"{self._context} ({pct:.1f}%)")
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

    def describe(self) -> str:
        """Full metadata for /status, independent of screen width."""
        return " | ".join((self._activity, *self._metadata))
