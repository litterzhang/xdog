"""Nonblocking, owner/registration/draft/cursor-fenced argument suggestions."""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from xdog.tui.autocomplete import CompletionItem
from xdog.tui.utils import sanitize_terminal_text


@dataclass(frozen=True)
class ArgumentRequest:
    serial: int
    owner: str
    command: str
    identity: str
    text: str
    cursor: int
    start: int
    end: int
    prefix: str


    def context(self) -> dict[str, Any]:
        """Coordinates relative to the captured argument body, never the host/session namespace."""
        offset = self.text.index(' ') + 1
        return {'arguments': self.text[offset:], 'cursor': self.cursor - offset,
                'start': self.start - offset, 'end': self.end - offset}


class ArgumentCompletionFeed:
    """UI-owned state; at most one request in flight plus one latest queued draft."""
    def __init__(self, owner: Callable[[], str], catalog: Callable[[], Mapping[str, str]],
                 send: Callable[[ArgumentRequest], None],
                 cancel: Callable[[ArgumentRequest], None] | None = None) -> None:
        self.owner, self.catalog, self.send = owner, catalog, send
        self.cancel = cancel
        self._canceling: ArgumentRequest | None = None
        self._serial = 0
        self._request: ArgumentRequest | None = None
        self._inflight: ArgumentRequest | None = None
        self._rows: tuple[CompletionItem, ...] = ()
        self._status = ''
        self._closed = False
        self._blocked: tuple[str, int] | None = None

    def lookup(self, text: str, cursor: int) -> tuple[tuple[int, int, str], tuple[CompletionItem, ...], str] | None:
        if self._closed or self._blocked == (text, cursor):
            return None
        self._blocked = None
        span = argument_span(text, cursor, self.catalog())
        if span is None:
            self.invalidate()
            return None
        command, identity, start, end, prefix = span
        owner = self.owner()
        old = self._request
        if old is None or (old.owner, old.command, old.identity, old.text, old.cursor) != (
                owner, command, identity, text, cursor):
            self._serial += 1
            self._request = ArgumentRequest(self._serial, owner, command, identity, text, cursor, start, end, prefix)
            self._rows, self._status = (), 'Reading registered arguments · Esc dismisses'
            self._dispatch()
        return (start, end, prefix), self._rows, self._status

    def _dispatch(self) -> None:
        if not self._closed and self._inflight is None and self._request is not None:
            self._inflight = self._request
            self.send(self._request)

    def publish(self, request: ArgumentRequest, rows: list[dict[str, Any]] | None, status: str = '') -> None:
        if self._inflight != request:
            return
        self._inflight = None
        self._canceling = None
        current = self._request
        if (not self._closed and current == request and self.owner() == request.owner
                and self.catalog().get(request.command) == request.identity):
            accepted: list[CompletionItem] = []
            seen: set[str] = set()
            if rows is not None:
                valid = isinstance(rows, list) and len(rows) <= 32
                for row in rows if valid else []:
                    if not isinstance(row, dict) or set(row) - {'value', 'label', 'description'}:
                        valid = False
                        break
                    value = row.get('value')
                    label = row.get('label', value)
                    detail = row.get('description', '')
                    for field, maximum in ((value, 2048), (label, 2048), (detail, 4096)):
                        if (not isinstance(field, str) or len(field.encode('utf-8', errors='surrogatepass')) > maximum
                                or sanitize_terminal_text(field) != field
                                or any(unicodedata.category(char) in {'Cc', 'Cs', 'Cf', 'Zl', 'Zp'} for char in field)):
                            valid = False
                            break
                    if not valid or not value or value in seen:
                        valid = False
                        break
                    assert isinstance(value, str) and isinstance(label, str) and isinstance(detail, str)
                    seen.add(value)
                    accepted.append(CompletionItem(value, label=label, detail=detail))
                if valid and len(json.dumps(rows, ensure_ascii=True).encode()) > 64 * 1024:
                    valid = False
                if not valid:
                    accepted = []
                    status = 'Invalid argument suggestions refused'
            self._rows = tuple(accepted)
            self._status = status or ('Select argument · Tab/Enter inserts only' if accepted else 'No argument matches')
        elif current is not None and current != request:
            self._dispatch()  # One latest request, never an unbounded keystroke job queue.

    def valid(self, text: str, cursor: int) -> bool:
        request = self._request
        return (request is not None and request.text == text and request.cursor == cursor
                and request.owner == self.owner() and self.catalog().get(request.command) == request.identity)

    def reopen(self) -> None:
        self._blocked = None

    def dismiss(self, text: str, cursor: int) -> None:
        self.invalidate()
        self._blocked = text, cursor

    def invalidate(self) -> None:
        pending = self._inflight
        if pending is not None and self.cancel is not None and self._canceling != pending:
            self._canceling = pending
            self.cancel(pending)  # Keep the slot until its actual settlement receipt arrives.
        self._serial += 1
        self._request, self._rows, self._status = None, (), ''

    def reset(self) -> None:
        self.invalidate()
        self._inflight = self._canceling = None

    def close(self) -> None:
        self._closed = True
        self.invalidate()


def argument_span(text: str, cursor: int, catalog: Mapping[str, str]) -> tuple[str, str, int, int, str] | None:
    if not text.startswith('/') or '\n' in text or len(text.encode()) > 8192 or not 0 <= cursor <= len(text):
        return None
    space = text.find(' ')
    if space < 0 or cursor <= space:
        return None
    command = text[1:space].lower()
    identity = catalog.get(command)
    if not isinstance(identity, str) or re.fullmatch(r'[a-f0-9]{32}', identity) is None:
        return None
    # Tokens use quotes and backslash escapes only to locate boundaries. Prefix
    # and full replacement values remain literal: no unquoting, expansion or
    # implicit suffix concatenation. A backslash is literal inside single quotes.
    start = space + 1
    while start < len(text):
        if text[start].isspace():
            if cursor == start:
                return command, identity, cursor, cursor, ''
            start += 1
            continue
        end, quote, escaped = start, '', False
        while end < len(text):
            char = text[end]
            if escaped:
                escaped = False
            elif char == '\\' and quote != "'":
                escaped = True
            elif char.isspace() and not quote:
                break
            elif char in ('"', "'"):
                quote = '' if quote == char else char if not quote else quote
            end += 1
        if start <= cursor <= end:
            return command, identity, start, end, text[start:cursor]
        start = end
    if cursor == len(text) and (not text[space + 1:] or text[-1].isspace()):
        return command, identity, cursor, cursor, ''
    return None
