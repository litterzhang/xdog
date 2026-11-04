"""Settings list component -- label/value settings UI with navigation.

Provides a scrollable list of settings with label/value pairs,
value cycling, submenu support, and optional fuzzy search.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from xdog.tui.fuzzy import fuzzy_filter
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import Component
from xdog.tui.utils import string_width, truncate_to_width, wrap_text_with_ansi


@dataclass
class SettingItem:
    """A single setting entry."""

    id: str
    label: str
    current_value: str
    description: str | None = None
    values: list[str] | None = None
    submenu: Callable[[str, Callable[[str | None], None]], Component] | None = None


@dataclass(frozen=True)
class SettingsListTheme:
    """Theme for the settings list."""

    label: Callable[[str, bool], str] = lambda text, _: text
    value: Callable[[str, bool], str] = lambda text, _: text
    description: Callable[[str], str] = lambda text: text
    cursor: str = "> "
    hint: Callable[[str], str] = lambda text: text


class SettingsList(Component):
    """Scrollable settings list with label/value pairs.

    Features:
    - Arrow key navigation with scroll window
    - Enter/Space cycles through ``values`` list
    - Submenu support (renders submenu component when active)
    - Optional fuzzy search filtering
    """

    def __init__(
        self,
        items: list[SettingItem],
        max_visible: int,
        theme: SettingsListTheme,
        on_change: Callable[[str, str], None],
        on_cancel: Callable[[], None],
        *,
        enable_search: bool = False,
    ) -> None:
        self._items = items
        self._filtered_items = list(items)
        self._max_visible = max_visible
        self._theme = theme
        self._on_change = on_change
        self._on_cancel = on_cancel
        self._selected_index = 0
        self._search_enabled = enable_search
        self._search_query = ""

        # Submenu state
        self._submenu_component: Component | None = None
        self._submenu_item_index: int | None = None

    def update_value(self, setting_id: str, new_value: str) -> None:
        """Update a setting's current value by ID."""
        for item in self._items:
            if item.id == setting_id:
                item.current_value = new_value
                break

    def invalidate(self) -> None:
        if self._submenu_component is not None:
            self._submenu_component.invalidate()

    def render(self, width: int) -> list[str]:
        if self._submenu_component is not None:
            return self._submenu_component.render(width)
        return self._render_main_list(width)

    def _render_main_list(self, width: int) -> list[str]:
        lines: list[str] = []

        # Search input
        if self._search_enabled:
            search_display = f"  Search: {self._search_query}_"
            lines.append(truncate_to_width(search_display, width))
            lines.append("")

        if not self._items:
            lines.append(self._theme.hint("  No settings available"))
            if self._search_enabled:
                self._add_hint_line(lines, width)
            return lines

        display_items = self._filtered_items if self._search_enabled else self._items
        if not display_items:
            lines.append(truncate_to_width(self._theme.hint("  No matching settings"), width))
            self._add_hint_line(lines, width)
            return lines

        # Scrolling window
        start = max(0, min(
            self._selected_index - self._max_visible // 2,
            len(display_items) - self._max_visible,
        ))
        end = min(start + self._max_visible, len(display_items))

        # Max label width for alignment
        max_label_w = min(30, max((string_width(item.label) for item in self._items), default=0))

        for i in range(start, end):
            item = display_items[i]
            is_selected = i == self._selected_index

            prefix = self._theme.cursor if is_selected else "  "
            prefix_w = string_width(prefix)

            # Pad label for alignment
            pad = max(0, max_label_w - string_width(item.label))
            label_padded = item.label + " " * pad
            label_text = self._theme.label(label_padded, is_selected)

            separator = "  "
            used_w = prefix_w + max_label_w + string_width(separator)
            value_max_w = width - used_w - 2

            value_text = self._theme.value(truncate_to_width(item.current_value, value_max_w, ""), is_selected)
            lines.append(truncate_to_width(prefix + label_text + separator + value_text, width))

        # Scroll indicator
        if start > 0 or end < len(display_items):
            scroll_text = f"  ({self._selected_index + 1}/{len(display_items)})"
            lines.append(self._theme.hint(truncate_to_width(scroll_text, width - 2, "")))

        # Description for selected item
        selected = display_items[self._selected_index] if self._selected_index < len(display_items) else None
        if selected and selected.description:
            lines.append("")
            for desc_line in wrap_text_with_ansi(selected.description, width - 4):
                lines.append(self._theme.description(f"  {desc_line}"))

        self._add_hint_line(lines, width)
        return lines

    def handle_input(self, event: KeyEvent) -> bool:
        """Return True when the event was consumed here.

        Returning None — which this did — reads as falsy to the dispatcher in
        `TUI._dispatch`, which then hands the same key to the parent as well.
        So an arrow key moved the selection *and* was processed again upstream.
        """
        # Delegate to submenu if active
        if self._submenu_component is not None:
            return bool(self._submenu_component.handle_input(event))

        display_items = self._filtered_items if self._search_enabled else self._items

        if event.matches("up"):
            if display_items:
                self._selected_index = (self._selected_index - 1) % len(display_items)
            return True
        elif event.matches("down"):
            if display_items:
                self._selected_index = (self._selected_index + 1) % len(display_items)
            return True
        elif event.matches("enter") or (hasattr(event, "key") and event.key == " "):
            self._activate_item()
            return True
        elif event.matches("escape"):
            self._on_cancel()
            return True
        elif self._search_enabled:
            # Handle search input
            if event.matches("backspace"):
                self._search_query = self._search_query[:-1]
                self._apply_filter()
                return True
            elif isinstance(event.key, str) and len(event.key) == 1 and event.key.isprintable():
                self._search_query += event.key
                self._apply_filter()
                return True
        return False

    def _activate_item(self) -> None:
        display_items = self._filtered_items if self._search_enabled else self._items
        if not display_items or self._selected_index >= len(display_items):
            return

        item = display_items[self._selected_index]

        if item.submenu is not None:
            self._submenu_item_index = self._selected_index
            self._submenu_component = item.submenu(item.current_value, self._submenu_done(item))
        elif item.values:
            try:
                current_idx = item.values.index(item.current_value)
            except ValueError:
                current_idx = -1
            next_idx = (current_idx + 1) % len(item.values)
            item.current_value = item.values[next_idx]
            self._on_change(item.id, item.current_value)

    def _submenu_done(self, item: SettingItem) -> Callable[[str | None], None]:
        def done(selected_value: str | None = None) -> None:
            if selected_value is not None:
                item.current_value = selected_value
                self._on_change(item.id, selected_value)
            self._close_submenu()
        return done

    def _close_submenu(self) -> None:
        self._submenu_component = None
        if self._submenu_item_index is not None:
            self._selected_index = self._submenu_item_index
            self._submenu_item_index = None

    def _apply_filter(self) -> None:
        if self._search_query:
            labels = [item.label for item in self._items]
            matches = fuzzy_filter(self._search_query, labels)
            matched_labels = {m.text for m in matches}
            self._filtered_items = [item for item in self._items if item.label in matched_labels]
        else:
            self._filtered_items = list(self._items)
        self._selected_index = 0

    def _add_hint_line(self, lines: list[str], width: int) -> None:
        lines.append("")
        hint = (
            "  Type to search \u00b7 Enter/Space to change \u00b7 Esc to cancel"
            if self._search_enabled
            else "  Enter/Space to change \u00b7 Esc to cancel"
        )
        lines.append(truncate_to_width(self._theme.hint(hint), width))
