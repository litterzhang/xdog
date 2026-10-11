"""Searchable session/branch/checkpoint browser in the bounded transient tail."""
from __future__ import annotations

from collections.abc import Callable

from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.navigation import NavigationEntry, NavigationFeed
from xdog.tui.tui import CURSOR_MARKER, Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width


class NavigationPanel(Component):
    focused = False

    def __init__(self, provider: NavigationFeed, activate: Callable[[NavigationEntry], None],
                 on_close: Callable[[], None], *, scope: str = "sessions",
                 on_new: Callable[[], None] | None = None,
                 on_view: Callable[[NavigationEntry], None] | None = None) -> None:
        self.provider = provider
        self.activate = activate
        self.on_close = on_close
        self.on_new = on_new
        self.on_view = on_view
        self._scope = scope
        self._query = ""
        self._offset = 0
        self._selected = 0
        self._budget = 8
        self._filtering = False
        self._version = -1
        self._back: list[str] = []
        provider.request(scope)
        self._view = provider.snapshot()  # Initial view is the actual pending read, not a false empty result.

    def set_render_budget(self, rows: int) -> None:
        self._budget = max(0, min(12, rows))

    def poll(self) -> bool:
        view = self.provider.snapshot()
        if view.version == self._version:
            return False
        selected_id = (self._view.entries[self._selected].id
                       if self._view.entries and self._selected < len(self._view.entries) else "")
        self._view, self._version = view, view.version
        if not view.busy:
            self._offset = view.offset
            self._selected = next((i for i, entry in enumerate(view.entries) if entry.id == selected_id),
                                  min(self._selected, max(0, len(view.entries) - 1)))
        return True

    def _request(self) -> None:
        self.provider.request(self._scope, self._query, self._offset)

    def _search(self) -> None:
        self._offset = self._selected = 0
        self._request()

    def handle_paste(self, text: str) -> bool:
        if self._filtering:
            self._query += sanitize_terminal_text(text).replace("\n", " ")
            self._search()
        return True

    def handle_input(self, event: KeyEvent) -> bool:
        if event.event_type == KeyEventType.RELEASE:
            return True
        if event.matches("escape") or event.matches("ctrl+c"):
            if self._filtering and event.matches("escape"):
                self._filtering = False
            else:
                self.on_close()
            return True
        if self._filtering:
            if event.key == "enter":
                self._filtering = False
            elif event.key == "backspace":
                self._query = self._query[:-1]
                self._search()
            elif len(event.key) == 1 and not event.ctrl and not event.alt:
                self._query += sanitize_terminal_text(event.key)
                self._search()
            return True
        if event.matches("/"):
            self._filtering = True
        elif event.key in {"up", "down"}:
            delta = -1 if event.key == "up" else 1
            target = self._selected + delta
            if target < 0 and self._offset:
                self._offset = max(0, self._offset - 50)
                self._selected = 49
                self._request()
            elif target >= len(self._view.entries) and self._offset + len(self._view.entries) < self._view.total:
                self._offset += len(self._view.entries)
                self._selected = 0
                self._request()
            else:
                self._selected = min(max(0, target), max(0, len(self._view.entries) - 1))
        elif event.key in {"pageup", "pagedown"}:
            self._offset = max(0, self._offset + (-50 if event.key == "pageup" else 50))
            self._selected = 0
            self._request()
        elif event.matches("r"):
            self._request()
        elif event.matches("n") and self.on_new is not None and event.event_type == KeyEventType.PRESS:
            self.on_new()
        elif (event.matches("v") and self.on_view is not None and event.event_type == KeyEventType.PRESS
              and self.focused and not self._view.busy and self._view.entries):
            self.on_view(self._view.entries[self._selected])
        elif event.key in {"right", "enter"} and not self._view.busy and self._view.entries:
            entry = self._view.entries[self._selected]
            if event.key == "right" and entry.kind in {"session", "branch"}:
                self._back.append(self._scope)
                self._scope = f"tree|{entry.session_id}|{entry.branch_id}"
                self._query = ""
                self._search()
            elif event.key == "enter" and event.event_type == KeyEventType.PRESS:
                self.activate(entry)
        elif event.key == "left":
            self._scope = self._back.pop() if self._back else "sessions"
            self._query = ""
            self._search()
        return True

    def render(self, width: int) -> list[str]:
        if not self._budget:
            return []
        mode = "Sessions" if self._scope == "sessions" else "Branch/tree"
        phase = " · loading" if self._view.busy else ""
        rows = [truncate_to_width(
            f"{mode} · {self._view.total} entries{phase} · Enter activate · → tree · / search"
            + (" · v view (read-only)" if self.on_view is not None else ""), width,
        )]
        if self._filtering:
            query = truncate_to_width("/ " + self._query, max(0, width - 1), "")
            rows.append(query + (CURSOR_MARKER if self.focused else ""))
        if self._view.error:
            rows.append(truncate_to_width("Navigation error: " + sanitize_terminal_text(self._view.error), width))
        elif not self._view.entries:
            rows.append(truncate_to_width("No matching sessions/checkpoints · r refresh · Esc close", width))
        else:
            count = max(1, self._budget - len(rows))
            start = max(0, min(self._selected - count // 2, len(self._view.entries) - count))
            for index in range(start, min(start + count, len(self._view.entries))):
                entry = self._view.entries[index]
                prefix = "→" if index == self._selected else " "
                depth = "  " * min(entry.depth, 8)
                current = " *" if entry.active else ""
                rows.append(truncate_to_width(sanitize_terminal_text(
                    f"{prefix} {depth}[{entry.kind}] {entry.title}{current} · {entry.detail}",
                ), width))
        return rows[:self._budget]
