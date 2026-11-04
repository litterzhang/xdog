"""Select list component -- a scrollable list with single-item selection.

Ported from TypeScript select-list.ts to use string-based rendering model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

from xdog.tui.keys import KeyEvent
from xdog.tui.tui import Component
from xdog.tui.utils import clamp, truncate_to_width

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class SelectItem(Generic[T]):
    """An item in a SelectList."""
    label: str
    value: T
    description: str = ""
    disabled: bool = False


class SelectList(Component, Generic[T]):
    """Scrollable list with single-selection. Ported from TypeScript."""

    def __init__(
        self,
        items: list[SelectItem[T]] | None = None,
        max_visible: int = 10,
        *,
        selected_fn: Callable[[str], str] | None = None,
        description_fn: Callable[[str], str] | None = None,
        scroll_fn: Callable[[str], str] | None = None,
    ) -> None:
        self._items: list[SelectItem[T]] = list(items or [])
        self._selected_index: int = 0
        self._max_visible = max_visible
        self._selected_fn = selected_fn or (lambda t: f"\x1b[1m{t}\x1b[0m")
        self._description_fn = description_fn or (lambda t: f"\x1b[2m{t}\x1b[0m")
        self._scroll_fn = scroll_fn or (lambda t: f"\x1b[2m{t}\x1b[0m")
        self.on_select_cb: Callable[[SelectItem[T]], None] | None = None
        self.on_cancel_cb: Callable[[], None] | None = None

    @property
    def items(self) -> list[SelectItem[T]]:
        return list(self._items)

    @items.setter
    def items(self, value: list[SelectItem[T]]) -> None:
        self._items = list(value)
        self._selected_index = clamp(self._selected_index, 0, max(0, len(self._items) - 1))

    @property
    def selected_item(self) -> SelectItem[T] | None:
        if 0 <= self._selected_index < len(self._items):
            return self._items[self._selected_index]
        return None

    def invalidate(self) -> None:
        pass

    def render(self, width: int) -> list[str]:
        if not self._items:
            return [self._description_fn("  No items")]

        lines: list[str] = []
        visible = min(self._max_visible, len(self._items))
        start = max(0, min(
            self._selected_index - visible // 2,
            len(self._items) - visible,
        ))
        end = min(start + visible, len(self._items))

        for i in range(start, end):
            item = self._items[i]
            is_sel = i == self._selected_index
            display = item.label or str(item.value)

            if is_sel:
                prefix = "→ "
                max_w = max(1, width - 4)
                text = truncate_to_width(display, max_w, "")
                if item.description and width > 40:
                    desc = truncate_to_width(item.description, max(1, width - len(text) - 6), "")
                    line = self._selected_fn(f"{prefix}{text}  {desc}")
                else:
                    line = self._selected_fn(f"{prefix}{text}")
            else:
                prefix = "  "
                max_w = max(1, width - 4)
                text = truncate_to_width(display, max_w, "")
                if item.description and width > 40:
                    desc = truncate_to_width(item.description, max(1, width - len(text) - 6), "")
                    line = f"{prefix}{text}  {self._description_fn(desc)}"
                else:
                    line = f"{prefix}{text}"
            lines.append(line)

        if start > 0 or end < len(self._items):
            lines.append(self._scroll_fn(f"  ({self._selected_index + 1}/{len(self._items)})"))

        return lines

    def handle_input(self, event: KeyEvent) -> bool:
        if event.key in ("up", "k") and not event.ctrl:
            self._selected_index = max(0, self._selected_index - 1)
            return True
        if event.key in ("down", "j") and not event.ctrl:
            self._selected_index = min(len(self._items) - 1, self._selected_index + 1)
            return True
        if event.key in ("enter", " "):
            if self.on_select_cb and self.selected_item:
                self.on_select_cb(self.selected_item)
            return True
        if event.key == "escape" or (event.ctrl and event.key == "c"):
            if self.on_cancel_cb:
                self.on_cancel_cb()
            return True
        return False
