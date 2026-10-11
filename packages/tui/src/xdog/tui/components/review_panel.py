"""Bounded read-only changed-file and hunk navigation in the native-buffer tail."""
from __future__ import annotations

from collections.abc import Callable

from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.repository_review import RepositoryReview, ReviewFile, ReviewLane, ReviewSnapshot, display_path
from xdog.tui.tui import CURSOR_MARKER, Component
from xdog.tui.utils import string_width, truncate_to_width


class ReviewPanel(Component):
    focused = False

    def __init__(self, backend: RepositoryReview, on_close: Callable[[], None]) -> None:
        self.backend = backend
        self.on_close = on_close
        self._view = backend.snapshot()
        self._version = -1
        self._selected = ""
        self._lane: ReviewLane = "working"
        self._start = 0
        self._column = 0
        self._width = 80
        self._budget = 8
        self._query = ""
        self._filtering = False
        self._wanted: tuple[str, ReviewLane, int, int, int] | None = None
        self._match_key: tuple[int, str] | None = None
        self._matches: tuple[ReviewFile, ...] = ()
        backend.refresh()

    def set_render_budget(self, max_rows: int) -> None:
        self._budget = max(0, min(12, max_rows))

    def _files(self) -> tuple[ReviewFile, ...]:
        key = (id(self._view.files), self._query.casefold())
        if key != self._match_key:
            self._match_key = key
            self._matches = tuple(file for file in self._view.files
                                  if key[1] in (file.search_name or file.label.casefold()))
        return self._matches

    def _choose(self, file: ReviewFile) -> None:
        self._selected = file.key
        self._lane = "staged" if file.status[1:2] == " " and file.status[0:1] != " " else "working"
        self._start = self._column = 0
        self._wanted = None
        self._request_page()

    def _request_page(self) -> None:
        if not self._selected or (self._view.busy and self._view.operation in {"refresh", "enter", "back"}):
            return
        wanted = (self._selected, self._lane, self._start, self._column, max(1, self._width))
        if wanted != self._wanted:
            self._wanted = wanted
            self.backend.page(*wanted)

    def poll(self) -> bool:
        view = self.backend.snapshot()
        if view.version == self._version:
            return False
        previous = self._view
        self._view, self._version = view, view.version
        if previous.operation in {"refresh", "enter", "back"} and not view.busy:
            self._wanted = None
        files = self._files()
        if files and not any(file.key == self._selected for file in files):
            self._choose(files[0])
        elif not files:
            self._selected = ""
            self._wanted = None
        if (not view.busy and view.key == self._selected and view.lane == self._lane
                and view.column == self._column and view.width == self._width):
            self._start = view.start  # Adopt the backend's clamp after EOF/file changes.
            self._wanted = (self._selected, self._lane, self._start, self._column, self._width)
        self._request_page()
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
            elif len(event.key) == 1 and not event.ctrl and not event.alt:
                self._query += display_path(event.key)
            self._wanted = None
            files = self._files()
            if files and not any(file.key == self._selected for file in files):
                self._choose(files[0])
            if not files:
                self._selected = ""
            return True
        if event.matches("/"):
            self._filtering = True
            return True
        files = self._files()
        if event.key in {"up", "down", "left", "right"} and not event.shift:
            if files:
                index = next((i for i, file in enumerate(files) if file.key == self._selected), 0)
                index = min(max(0, index + (-1 if event.key in {"up", "left"} else 1)), len(files) - 1)
                self._choose(files[index])
            return True
        if event.shift and event.key in {"left", "right"}:
            self._column = max(0, self._column + (-20 if event.key == "left" else 20))
        elif event.key in {"pageup", "pagedown", "home", "end"}:
            step = max(1, self._budget - 4)
            self._start = (0 if event.key == "home" else max(0, self._view.total - step) if event.key == "end"
                           else max(0, self._start + (-step if event.key == "pageup" else step)))
        elif event.matches("n") or event.matches("p"):
            if self._view.key != self._selected or self._view.lane != self._lane or self._view.busy:
                return True
            if event.key == "n":
                self._start = next((row for row in self._view.hunks if row > self._start), self._start)
            else:
                self._start = next((row for row in reversed(self._view.hunks) if row < self._start), 0)
        elif event.matches("s") or event.matches("w"):
            self._lane = "staged" if event.key == "s" else "working"
            self._start = 0
        elif event.matches("r"):
            self._wanted = None
            self.backend.refresh()
            return True
        elif event.matches("d"):
            self.backend.enter(self._selected)
            return True
        elif event.matches("b"):
            self.backend.back()
            return True
        else:
            return True  # Focused review never edits/submits the underlying draft.
        self._request_page()
        return True

    def handle_paste(self, text: str) -> bool:
        if self._filtering:
            self._query += display_path(text.replace("\r", "").replace("\n", ""))
            files = self._files()
            if files and not any(file.key == self._selected for file in files):
                self._choose(files[0])
            elif not files:
                self._selected = ""
        return True

    def render(self, width: int) -> list[str]:
        if not self._budget:
            return []
        self._width = max(1, width)
        self._request_page()
        files = self._files()
        title = f"Review · {self._lane} · {len(files)}/{len(self._view.files)} files · read-only"
        if self._view.busy:
            title += " · loading"
        title += f" · {self._view.root}"
        rows = [truncate_to_width(title, width)]
        if self._filtering:
            label = "/ " + display_path(self._query)
            label = truncate_to_width(label, max(0, width - 1), "")
            rows.append(label + (CURSOR_MARKER if self.focused and string_width(label) < width else ""))
        elif self._view.error:
            rows.append(truncate_to_width("Review error: " + display_path(self._view.error), width))
        elif not files:
            rows.append(truncate_to_width("No matching changes · / filter · r refresh · Esc close", width))
        else:
            index = next((i for i, file in enumerate(files) if file.key == self._selected), 0)
            menu_rows = min(3, max(1, self._budget // 3))
            start = max(0, min(index - menu_rows // 2, len(files) - menu_rows))
            for i in range(start, min(len(files), start + menu_rows)):
                file = files[i]
                rows.append(truncate_to_width(f"{'→' if i == index else ' '} {file.status} {file.label}", width))
            if self._view.key == self._selected and self._view.lane == self._lane:
                rows.extend(
                    truncate_to_width(row, width, "") for row in self._view.rows[:max(0, self._budget - len(rows))]
                )
            else:
                rows.append(truncate_to_width("Loading selected patch…", width))
        return rows[:self._budget]

    @property
    def view(self) -> ReviewSnapshot:
        return self._view
