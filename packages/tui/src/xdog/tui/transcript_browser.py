"""Immutable full-text transcript snapshots and one coalescing browser worker.

No terminal rendering, application policy, provider calls or clipboard I/O here.
The worker indexes all retained source lines; only the requested viewport crosses
back to the UI. Search is literal and Unicode-case-insensitive, not a regex.
"""
from __future__ import annotations

import threading
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace

from xdog.tui.utils import sanitize_terminal_text, slice_by_column, string_width


@dataclass(frozen=True, slots=True)
class TranscriptRecord:
    id: str
    role: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class TranscriptRow:
    index: int
    record_id: str
    role: str
    line: int
    text: str
    matched: bool = False
    selected: bool = False


@dataclass(frozen=True, slots=True)
class TranscriptView:
    version: int = 0
    busy: bool = False
    rows: tuple[TranscriptRow, ...] = ()
    total: int = 0
    top: int = 0
    cursor: int = 0
    matches: int = 0
    match: int | None = None
    horizontal: int = 0
    anchor: int | None = None
    selection_end: int | None = None
    entry_start: int = 0
    entry_end: int = 0
    title: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class BrowserRequest:
    records: tuple[TranscriptRecord, ...] = ()
    query: str = ""
    cursor: int = 0
    top: int = 0
    width: int = 80
    height: int = 6
    horizontal: int = 0
    anchor: int | None = None
    match: int | None = None
    follow: bool = False
    entry_delta: int = 0
    select_entry: bool = False
    selection_end: int | None = None


class TranscriptBrowser:
    """Own a complete source index; newer requests fence unpublished old views."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._closed = False
        self._pending: tuple[int, BrowserRequest] | None = None
        self._serial = 0
        self._view = TranscriptView()
        self._thread: threading.Thread | None = None
        self._records: tuple[TranscriptRecord, ...] = ()
        self._lines: list[tuple[TranscriptRecord, int, str]] = []
        self._bounds: dict[str, tuple[int, int]] = {}
        self._query: str | None = None
        self._matches: list[int] = []
        self._cumulative: list[int] = []

    def request(self, request: BrowserRequest) -> int:
        with self._condition:
            if self._closed:
                return self._serial
            self._serial += 1
            self._pending = (self._serial, request)
            # Old rows are not actionable while a replacement is pending.
            self._view = replace(self._view, version=self._serial, busy=True, rows=())
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, daemon=True, name="xdog-transcript-browser")
                self._thread.start()
            self._condition.notify()
            return self._serial

    def snapshot(self) -> TranscriptView:
        with self._condition:
            return self._view

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify()

    def _obsolete(self, serial: int) -> bool:
        with self._condition:
            return self._closed or serial != self._serial

    def _work(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    self._records = ()
                    self._lines.clear()
                    self._bounds.clear()
                    self._matches.clear()
                    self._cumulative.clear()
                    return
                assert self._pending is not None
                serial, request = self._pending
                self._pending = None
            try:
                view = self._build(serial, request)
            except Exception as exc:
                view = TranscriptView(version=serial, error=str(exc))
            with self._condition:
                if not self._closed and serial == self._serial and view is not None:
                    self._view = view

    def _build(self, serial: int, request: BrowserRequest) -> TranscriptView | None:
        cursor_key = None
        if request.records != self._records:
            if self._lines:
                old_record, old_number, _text = self._lines[min(request.cursor, len(self._lines) - 1)]
                cursor_key = (old_record.id, old_number)
            lines: list[tuple[TranscriptRecord, int, str]] = []
            bounds = {}
            for record in request.records:
                if self._obsolete(serial):
                    return None
                start = len(lines)
                # Full source, not rendered Markdown or a collapsed tool preview.
                text = sanitize_terminal_text(record.body).replace("\t", "    ")
                lines.extend((record, number, line) for number, line in enumerate(text.split("\n"), 1))
                bounds[record.id] = (start, len(lines) - 1)
            self._records, self._lines, self._bounds = request.records, lines, bounds
            self._query = None
        if request.query != self._query:
            query = request.query.casefold()
            matches = []
            cumulative = []
            total_matches = 0
            if query:
                for index, (_record, _number, text) in enumerate(self._lines):
                    if index % 256 == 0 and self._obsolete(serial):
                        return None
                    count = text.casefold().count(query)
                    if count:
                        matches.append(index)
                        total_matches += count
                        cumulative.append(total_matches)
            self._matches, self._cumulative, self._query = matches, cumulative, request.query
        total = len(self._lines)
        cursor = min(max(0, request.cursor), max(0, total - 1))
        if cursor_key is not None and cursor_key[0] in self._bounds:
            start, end = self._bounds[cursor_key[0]]
            cursor = min(end, start + cursor_key[1] - 1)
        selected_match = None
        horizontal = max(0, request.horizontal)
        match_count = self._cumulative[-1] if self._cumulative else 0
        if request.match is not None and match_count:
            selected_match = request.match % match_count
            match_line = bisect_right(self._cumulative, selected_match)
            cursor = self._matches[match_line]
            prior = self._cumulative[match_line - 1] if match_line else 0
            text = self._lines[cursor][2]
            folded, query = text.casefold(), request.query.casefold()
            offset = -len(query)
            for _ in range(selected_match - prior + 1):
                offset = folded.find(query, offset + len(query))
            if text.isascii():
                column = offset
            else:
                folded_position = 0
                source_position = 0
                for char in text:
                    if folded_position + len(char.casefold()) > offset:
                        break
                    folded_position += len(char.casefold())
                    source_position += 1
                column = string_width(text[:source_position])
            if column < horizontal or column + string_width(query) > horizontal + request.width:
                horizontal = column
        if request.entry_delta and total:
            step = 1 if request.entry_delta > 0 else -1
            for _ in range(min(abs(request.entry_delta), len(self._records))):
                start, end = self._bounds[self._lines[cursor][0].id]
                cursor = min(total - 1, end + 1) if step > 0 else max(0, start - 1)
            selected_match = None
        if request.follow:
            cursor = max(0, total - 1)
        height = max(1, request.height)
        top = min(max(0, request.top), max(0, total - height))
        if cursor < top:
            top = cursor
        elif cursor >= top + height:
            top = max(0, cursor - height + 1)
        entry_start = entry_end = 0
        title = ""
        if total:
            record = self._lines[cursor][0]
            entry_start, entry_end = self._bounds[record.id]
            title = sanitize_terminal_text(f"{record.role}: {record.title}").replace("\n", " ")
        anchor, selection_end = request.anchor, request.selection_end
        if request.select_entry and total:
            anchor, selection_end = entry_start, entry_end
        end = selection_end if selection_end is not None else cursor
        low = min(anchor, end) if anchor is not None else -1
        high = max(anchor, end) if anchor is not None else -1
        matched = set(self._matches[bisect_left(self._matches, top):bisect_left(self._matches, top + height)])
        rows = tuple(TranscriptRow(
            index=index, record_id=record.id, role=record.role, line=number,
            text=(text[horizontal:horizontal + max(1, request.width)] if text.isascii()
                  else slice_by_column(text, horizontal, horizontal + max(1, request.width))),
            matched=index in matched, selected=low <= index <= high,
        ) for index, (record, number, text) in enumerate(self._lines[top:top + height], top))
        return TranscriptView(version=serial, rows=rows, total=total, top=top, cursor=cursor,
                              matches=match_count, match=selected_match, title=title, horizontal=horizontal,
                              entry_start=entry_start, entry_end=entry_end,
                              anchor=anchor, selection_end=selection_end)

    @staticmethod
    def selected_text(records: tuple[TranscriptRecord, ...], low: int, high: int) -> str:
        """Extract complete source lines for explicit copy/export (call off UI)."""
        parts = []
        index = 0
        for record in records:
            for line in sanitize_terminal_text(record.body).split("\n"):
                if low <= index <= high:
                    parts.append(line)
                index += 1
                if index > high:
                    return "\n".join(parts)
        return "\n".join(parts)
