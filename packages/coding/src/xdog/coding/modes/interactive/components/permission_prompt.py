"""Interactive tool-permission approval panel."""

from __future__ import annotations

from collections.abc import Callable

from xdog.coding.core.permissions import PermissionDecision, PermissionRequest
from xdog.coding.modes.interactive.theme import Theme
from xdog.tui.components.select_list import SelectItem, SelectList
from xdog.tui.editor_layout import layout_editor
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width


class PermissionPromptComponent(Component):
    """Focused inline panel that resolves one permission request."""

    def __init__(
        self,
        request: PermissionRequest,
        theme: Theme,
        on_decision: Callable[[PermissionDecision], None],
    ) -> None:
        self.request = request
        self._on_decision = on_decision
        self._theme = theme
        self._budget = 12
        self._scroll = 0
        self._summary_rows = 1
        self._summary_length = 1

        self._choices: SelectList[PermissionDecision] = SelectList(
            [
                SelectItem("Allow once", "allow_once", "Run only this call"),
                SelectItem(
                    "Allow for this session",
                    "allow_session",
                    "Remember this exact call",
                ),
                SelectItem("Deny", "deny", "Return a denial to the model"),
            ],
            max_visible=3,
            selected_fn=theme.accent,
            description_fn=theme.dim,
            scroll_fn=theme.dim,
        )
        self._choices.on_select_cb = lambda item: self._on_decision(item.value)
        self._choices.on_cancel_cb = lambda: self._on_decision("deny")

    def set_render_budget(self, max_rows: int) -> None:
        self._budget = max(0, max_rows)

    def render(self, width: int) -> list[str]:
        if not self._budget:
            return []
        # Show every choice when there is also room for the title and summary.
        # In short terminals, keep the selected action reachable.
        selected = self._choices.selected_item
        assert selected is not None
        hint = "↑/↓ choose · Enter · Esc deny · PgUp/PgDn summary"
        if self._budget >= 5:
            actions = self._choices.render(width)
            if self._budget >= 6:
                actions.append(self._theme.dim(hint))
        else:
            actions = [self._theme.accent(f"→ {selected.label} · {hint}")]
        rows = []
        if self._budget >= 3:
            rows.append(self._theme.bold("Tool permission required"))
        self._summary_rows = max(0, self._budget - len(rows) - len(actions))
        summary = layout_editor(sanitize_terminal_text(self.request.summary), max(1, width))
        self._summary_length = len(summary.rows)
        self._scroll = min(self._scroll, max(0, self._summary_length - self._summary_rows))
        if self._summary_rows:
            visible = [row.text for row in summary.rows[self._scroll:self._scroll + self._summary_rows]]
            rows.extend(visible)
        rows.extend(actions)
        return [truncate_to_width(row, max(0, width), "") for row in rows]

    def handle_input(self, event: KeyEvent) -> bool:
        if event.key in ("pageup", "pagedown"):
            # One row of overlap keeps the line carrying the scroll hint reachable.
            step = max(1, self._summary_rows - 1)
            delta = step if event.key == "pagedown" else -step
            self._scroll = min(max(0, self._scroll + delta), max(0, self._summary_length - self._summary_rows))
            return True
        return self._choices.handle_input(event)

    def invalidate(self) -> None:
        pass
