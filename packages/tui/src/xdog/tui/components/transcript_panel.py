"""Full-history source-text search, scrolling and line/entry selection."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from xdog.tui.components.panel import ThemedPanel
from xdog.tui.editor_layout import grapheme_clusters
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.transcript_browser import BrowserRequest, TranscriptBrowser, TranscriptRecord
from xdog.tui.tui import CURSOR_MARKER
from xdog.tui.utils import sanitize_terminal_text, slice_by_column, string_width


class TranscriptPanel(ThemedPanel):
    focused = False
    caption = "Transcript"
    content_label = "transcript"
    source_label = "full gateway source"

    def __init__(self, records: Callable[[], tuple[TranscriptRecord, ...]], on_close: Callable[[], None],
                 on_fullscreen: Callable[[], None], *, query: str = "",
                 on_refresh: Callable[[], None] | None = None,
                 on_output: Callable[[str, str], None] | None = None) -> None:
        self._provider = records
        self._on_refresh = on_refresh
        self._on_output = on_output
        self._source_busy = False
        self._source_status = ""
        self._source_follow_pending = False
        self.on_close, self.on_fullscreen = on_close, on_fullscreen
        self.browser = TranscriptBrowser()
        self._budget = 8
        self.fullscreen = False
        self._filtering = False
        query = sanitize_terminal_text(query).replace("\n", " ")
        self._request = BrowserRequest(records=records(), query=query, match=0 if query else None)
        self._serial = self.browser.request(self._request)
        self._view = self.browser.snapshot()
        self._version = -1

    def _submit(self, **changes: Any) -> None:
        if "query" in changes:
            self._source_follow_pending = False
            changes["follow"] = False
        self._request = replace(self._request, **changes)
        self._serial = self.browser.request(self._request)

    @property
    def searching(self) -> bool:
        """Whether printable keys currently belong to the literal query editor."""
        return self._filtering

    def search(self, query: str) -> None:
        query = sanitize_terminal_text(query).replace("\n", " ")
        self._submit(query=query, match=0, anchor=None, selection_end=None, select_entry=False)

    def set_source_status(self, message: str, *, busy: bool = False) -> None:
        self._source_status = sanitize_terminal_text(message).replace("\n", " ")
        self._source_busy = busy
        if busy and (self._request.anchor is not None or self._request.select_entry):
            self._submit(anchor=None, selection_end=None, select_entry=False)

    def replace_records(self, records: tuple[TranscriptRecord, ...]) -> None:
        self._source_busy = False
        follow = self._source_follow_pending or self._request.follow
        self._source_follow_pending = False
        self._submit(records=records, anchor=None, selection_end=None, select_entry=False, follow=follow)

    def refresh(self) -> None:
        if self._on_refresh is not None:
            self._on_refresh()
        else:
            self.replace_records(self._provider())

    def close(self) -> None:
        self.browser.close()

    def poll(self) -> bool:
        view = self.browser.snapshot()
        if view.version == self._version and view.busy == self._view.busy:
            return False
        self._view, self._version = view, view.version
        if not view.busy and not view.error:
            self._request = replace(self._request, cursor=view.cursor, top=view.top, horizontal=view.horizontal,
                                    anchor=view.anchor, selection_end=view.selection_end, select_entry=False,
                                    entry_delta=0, follow=False,
                                    match=(None if self._request.entry_delta else
                                           view.match if view.match is not None and not self._source_busy
                                           else self._request.match))
        return True

    @property
    def interactive_query(self) -> bool:
        return self._filtering or bool(self._request.query)

    @property
    def selection(self) -> tuple[int, int] | None:
        if (self._source_busy or self._request.anchor is None or self._view.busy or self.browser.snapshot().busy
                or self._view.version != self._serial or self._view.error):
            return None
        end = self._request.selection_end if self._request.selection_end is not None else self._view.cursor
        return min(self._request.anchor, end), max(self._request.anchor, end)

    @property
    def settled(self) -> bool:
        return (not self._source_busy and not self._view.busy and not self.browser.snapshot().busy
                and self._view.version == self._serial and not self._view.error)

    @property
    def entry_id(self) -> str:
        if not self.settled:
            return ""
        return next((row.record_id for row in self._view.rows if row.index == self._view.cursor), "")

    @property
    def records(self) -> tuple[TranscriptRecord, ...]:
        return self._request.records

    def set_render_budget(self, rows: int) -> None:
        self._budget = max(0, rows if self.fullscreen else min(12, rows))

    def set_height(self, height: int) -> None:
        self.set_render_budget(height)

    def handle_paste(self, text: str) -> bool:
        if self._filtering:
            self._submit(query=self._request.query + sanitize_terminal_text(text).replace("\n", " "),
                         match=0, anchor=None, selection_end=None, select_entry=False)
        return True

    def handle_input(self, event: KeyEvent) -> bool:
        if event.event_type == KeyEventType.RELEASE:
            return True
        if event.matches("ctrl+c"):
            self.on_close()
        elif event.matches("escape"):
            if self._filtering:
                self._filtering = False
            elif self._request.anchor is not None:
                self._submit(anchor=None, selection_end=None)
            else:
                self.on_close()
        elif self._filtering:
            if event.matches("enter"):
                self._filtering = False
            elif event.matches("backspace"):
                clusters = grapheme_clusters(self._request.query)
                self._submit(query=self._request.query[:clusters[-1][0]] if clusters else "",
                             match=0, anchor=None, selection_end=None, select_entry=False)
            elif len(event.key) == 1 and not event.ctrl and not event.alt:
                self.handle_paste(event.key)
        elif event.matches("/"):
            self._filtering = True
        elif event.matches("f"):
            self.on_fullscreen()
        elif event.matches("r"):
            self.refresh()
        elif event.matches("n") or event.matches("shift+n") or event.matches("N"):
            delta = -1 if event.shift or event.key == "N" else 1
            self._submit(match=(self._request.match or 0) + delta)
        elif self._source_busy:
            return True  # Query/screen/refresh/close remain usable, not old-source selection.
        elif self._on_output is not None and event.key in {"y", "e", "s"} and not event.ctrl and not event.alt:
            if self.settled:
                action = {"y": "copy", "e": "export", "s": "share"}[event.key]
                scope = "selection" if self.selection is not None else "conversation" if action == "share" else "entry"
                self._on_output(action, scope)
        elif event.matches("v"):
            self._submit(anchor=self._request.cursor if self._request.anchor is None else None, selection_end=None)
        elif event.matches("a"):
            self._submit(select_entry=True)
        elif event.matches("end"):
            if self._on_refresh is not None:
                self._source_follow_pending = True
                self._request = replace(self._request, follow=True, match=None, anchor=None,
                                        selection_end=None, select_entry=False)
                self.refresh()
            else:
                self._submit(records=self._provider(), follow=True, match=None, anchor=None,
                             selection_end=None, select_entry=False)
        elif event.key in {"up", "down", "pageup", "pagedown", "home", "left", "right"}:
            if event.key in {"left", "right"} and event.shift:
                self._submit(horizontal=max(0, self._request.horizontal + (-8 if event.key == "left" else 8)),
                             match=None)
            elif event.key in {"left", "right"}:
                self._submit(entry_delta=self._request.entry_delta + (-1 if event.key == "left" else 1),
                             selection_end=None)
            else:
                step = max(1, self._request.height - 1) if event.key in {"pageup", "pagedown"} else 1
                target = self._request.cursor + (-step if event.key in {"up", "pageup"} else step)
                if event.key == "home":
                    target = 0
                elif event.key == "end":
                    target = max(0, self._view.total - 1)
                anchor = self._request.anchor
                if event.shift and anchor is None:
                    anchor = self._request.cursor
                self._submit(cursor=max(0, target), match=None, anchor=anchor, selection_end=None)
        return True

    def render(self, width: int) -> list[str]:
        if not self._budget:
            return []
        body_rows = max(1, self._budget - 2)
        body_width = max(1, width - 3)
        if (body_width, body_rows) != (self._request.width, self._request.height):
            self._submit(width=body_width, height=body_rows)
        view = self._view
        if self._source_busy:
            body = [f"Loading {self.source_label}… (Esc closes without canceling agent work)"]
        elif view.busy or view.version != self._serial or self.browser.snapshot().busy:
            body = [f"Loading {self.content_label}…"]
        elif view.error:
            body = [self.panel_theme.error(f"{self.caption} error: {view.error}")]
        else:
            body = []
            for row in view.rows:
                prefix = "▶" if row.index == view.cursor else " "
                prefix += "*" if row.matched else " "
                text = f"{prefix} {row.text}"
                style = (lambda value: self.panel_theme.inverse(self.panel_theme.accent(value))) if row.selected else (
                    self.panel_theme.accent if row.index == view.cursor else
                    self.panel_theme.success if row.matched else self.panel_theme.fg)
                body.append(style(text))
            if not body:
                body = [f"No {self.content_label} available"]
        mode = "fullscreen" if self.fullscreen else "inline"
        header = f"{self.caption} ({mode})"
        selected = self.selection
        if selected:
            header += f" · selected {selected[1] - selected[0] + 1} lines"
        if self._source_status:
            header += f" · {self._source_status}"
        header += f" · {view.cursor + 1 if view.total else 0}/{view.total} · {view.title}"
        match = ("pending" if self._source_busy else
                 f"{view.match + 1}/{view.matches}" if view.match is not None else str(view.matches))
        if self._filtering:
            query_width = max(0, width - 9)
            query_cells = string_width(self._request.query)
            visible_query = slice_by_column(self._request.query, max(0, query_cells - query_width), query_cells)
            footer = f"Search /{visible_query}{CURSOR_MARKER}"
        else:
            footer = (f"Search /{self._request.query} · {match} matches · n/N · v mark · a entry · "
                      "y copy · e export · s share · f screen · r refresh · Esc close")
        if self._budget == 1:
            return self._styled_rows([footer if self._filtering else header], width)
        rows = [header, *body[:body_rows]]
        if self._budget > 2:
            rows.append(footer)
        return self._styled_rows(rows[:self._budget], width)
