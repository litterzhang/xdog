"""EditorComponent protocol -- interface for pluggable editor implementations.

Allows extensions to provide custom editor implementations (e.g., vim mode,
emacs mode, custom keybindings) while maintaining compatibility with the
core application.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:
    from xdog.tui.autocomplete import CompletionProvider
    from xdog.tui.keys import KeyEvent


@runtime_checkable
class EditorComponent(Protocol):
    """Interface for custom editor components.

    Required methods must be implemented.  Optional methods/attributes
    may be omitted -- callers must check with ``hasattr`` before use.
    """

    # =========================================================================
    # Core text access (required)
    # =========================================================================

    def get_text(self) -> str:
        """Return the current text content."""
        ...

    def set_text(self, text: str) -> None:
        """Replace the current text content."""
        ...

    def handle_input(self, event: KeyEvent) -> bool:
        """Handle a parsed key event and report whether it was consumed."""
        ...

    def render(self, width: int) -> list[str]:
        """Render the component to lines for the given viewport width."""
        ...

    def invalidate(self) -> None:
        """Invalidate any cached rendering state."""
        ...

    # =========================================================================
    # Callbacks (optional -- set by the host application)
    # =========================================================================

    on_submit: Callable[[str], None] | None
    on_change: Callable[[str], None] | None

    # =========================================================================
    # History support (optional)
    # =========================================================================

    def add_to_history(self, text: str) -> None:
        """Add *text* to history for up/down navigation."""
        ...

    def reset_history(self) -> None:
        """Clear history and any in-progress navigation state."""
        ...

    # =========================================================================
    # Advanced text manipulation (optional)
    # =========================================================================

    def insert_text_at_cursor(self, text: str) -> None:
        """Insert *text* at the current cursor position."""
        ...

    def get_expanded_text(self) -> str:
        """Return text with any markers expanded (e.g., paste markers).

        Falls back to :meth:`get_text` if not implemented.
        """
        ...

    # =========================================================================
    # Autocomplete support (optional)
    # =========================================================================

    def set_autocomplete_provider(self, provider: CompletionProvider) -> None:
        """Set the autocomplete provider."""
        ...

    # =========================================================================
    # Appearance (optional)
    # =========================================================================

    border_color: Callable[[str], str] | None

    def set_padding_x(self, padding: int) -> None:
        """Set horizontal padding."""
        ...

    def set_autocomplete_max_visible(self, max_visible: int) -> None:
        """Set max visible items in autocomplete dropdown."""
        ...

    def set_render_budget(self, max_rows: int, show_borders: bool = True) -> None:
        """Set the total rows available for editor and autocomplete output."""
        ...
