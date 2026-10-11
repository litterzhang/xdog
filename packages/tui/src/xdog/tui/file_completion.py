"""Non-blocking, bounded filesystem suggestions for prompt editors.

Only names are read: completing a reference never reads file contents or grants
permission to tools. One lazily started daemon worker coalesces requests; its
immutable results are consumed by the editor on the UI thread.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Generator, Sequence
from pathlib import Path

from xdog.tui.autocomplete import CompletionItem
from xdog.tui.fuzzy import fuzzy_match
from xdog.tui.workspace_index import WorkspaceIndex, walk_paths


def _quote(path: str) -> str:
    if any(char.isspace() or char in {'"', "\\"} for char in path):
        return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return path


def _unquote(path: str) -> str:
    if path.startswith('"'):
        path = path[1:]
        if path.endswith('"'):
            path = path[:-1]
        path = path.replace('\\"', '"').replace("\\\\", "\\")
    return path


class WorkspaceFileCompletionProvider:
    """Asynchronous fuzzy references and path browsing without a total entry cap.

    ``max_entries`` and ``scan_seconds`` bound each worker batch, not the total
    discovered workspace. Git handles ignore rules; missing Git is reported as
    basic-exclusion fallback. Explicit paths deliberately bypass ignore rules.
    """

    def __init__(
        self, root: str | Path, *, max_entries: int = 20_000, max_results: int = 20,
        scan_seconds: float = 0.2, refresh_seconds: float = 3.0,
    ) -> None:
        self.root = Path(os.path.abspath(os.path.expanduser(str(root))))
        self.max_entries = max(1, max_entries)
        self.max_results = max(1, max_results)
        self.scan_seconds = max(0.001, scan_seconds)
        self.refresh_seconds = max(0.1, refresh_seconds)
        self._condition = threading.Condition()
        self._pending: str | None = None
        self._requested: str | None = None
        self._completed: str | None = None
        self._items: tuple[CompletionItem, ...] = ()
        self._status = ""
        self._status_prefix: str | None = None
        self._requested_at = 0.0
        self._closed = False
        self._thread: threading.Thread | None = None
        self._workspace: WorkspaceIndex | None = None
        self._indexed_at = 0.0
        self._background = False
        self._next_background = False
        self._search_prefix: str | None = None
        self._search_generation = -1
        self._position = 0
        self._best: list[tuple[int, str]] = []
        self._directory_scan: Generator[str | None, None, None] | None = None
        self._directory_done = False
        self._next_status = ""

    def get_completions(self, prefix: str) -> Sequence[CompletionItem]:
        with self._condition:
            if self._closed:
                return ()
            if prefix != self._requested or (
                prefix == self._completed and not self._background
                and time.monotonic() - self._requested_at >= self.refresh_seconds
            ):
                self._requested = prefix
                self._requested_at = time.monotonic()
                self._pending = prefix
                if self._thread is None:
                    self._thread = threading.Thread(target=self._work, name="xdog-file-completion", daemon=True)
                    self._thread.start()
                self._condition.notify()
            return self._items if prefix == self._completed else ()

    def get_completion_status(self, prefix: str) -> str:
        """An immutable progress/fallback snapshot; safe to call on the UI thread."""
        with self._condition:
            if self._closed:
                return ""
            return self._status if prefix == self._status_prefix else "Looking up paths…"

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify()

    def _work(self) -> None:
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or self._pending is not None or self._background)
                    if self._closed:
                        return
                    prefix = self._pending if self._pending is not None else self._requested
                    self._pending = None
                assert prefix is not None
                try:
                    items = self._complete(prefix)
                except (OSError, ValueError, sqlite3.Error) as exc:
                    items = ()
                    self._next_background = False
                    self._next_status = f"Path lookup failed: {exc}"
                with self._condition:
                    if self._closed:
                        return
                    was_background = self._background
                    self._background = self._next_background
                    if prefix == self._requested:
                        if was_background and not self._background:
                            self._requested_at = time.monotonic()
                        self._completed, self._items = prefix, tuple(items)
                        self._status_prefix, self._status = prefix, self._next_status
                    # Yield between batches; newer prefixes and exit wake us early.
                    if self._background and self._pending is None:
                        self._condition.wait(timeout=0.005)
        finally:
            if self._directory_scan is not None:
                self._directory_scan.close()
            if self._workspace is not None:
                self._workspace.close()

    def _current(self, prefix: str) -> bool:
        with self._condition:
            return not self._closed and self._requested == prefix

    def _rank(self, score: int, path: str) -> None:
        self._best.append((score, path))
        self._best.sort(key=lambda item: (-item[0], not item[1].endswith("/"), item[1]))
        del self._best[self.max_results:]

    def _reset_search(self, prefix: str) -> None:
        if self._directory_scan is not None:
            self._directory_scan.close()
            self._directory_scan = None
        self._search_prefix = prefix
        self._position = 0
        self._best = []
        self._directory_done = False

    def _complete(self, prefix: str) -> Sequence[CompletionItem]:
        marker = "@" if prefix.startswith("@") else ""
        query = _unquote(prefix[len(marker):])
        explicit = query.startswith(("./", "../", "~/", "/")) or (
            "/" in query and (not marker or query.endswith("/"))
        )
        if marker and not explicit:
            if self._workspace is None:
                self._workspace = WorkspaceIndex(self.root)
                self._workspace.begin()
            index = self._workspace
            if not index.scanning and time.monotonic() - self._indexed_at >= self.refresh_seconds:
                index.begin()
            was_scanning = index.scanning
            index.advance(self.max_entries, self.scan_seconds)
            if was_scanning and not index.scanning:
                self._indexed_at = time.monotonic()
            if prefix != self._search_prefix or index.generation != self._search_generation:
                self._reset_search(prefix)
                self._search_generation = index.generation
            deadline = time.monotonic() + self.scan_seconds
            processed = 0
            while processed < self.max_entries and time.monotonic() < deadline and self._current(prefix):
                rows = index.rows_after(self._position, min(256, self.max_entries - processed))
                if not rows:
                    break
                for position, path in rows:
                    match = fuzzy_match(query, path)
                    if match is not None:
                        self._rank(match.score, path)
                    self._position = position
                    processed += 1
                    if time.monotonic() >= deadline:
                        break
            self._next_background = index.scanning or self._position < index.max_rowid
            phase = "Indexing" if self._next_background else "Indexed"
            self._next_status = f"{phase} {index.count:,} paths · {index.mode}"
            if index.error:
                self._next_status += f" · {index.error}"
            paths = [path for _score, path in self._best]
        elif explicit:
            if prefix != self._search_prefix or self._directory_done:
                self._reset_search(prefix)
                parent, _, _name = query.rpartition("/")
                directory = Path(os.path.expanduser(parent + "/"))
                if not directory.is_absolute():
                    directory = self.root / directory
                self._directory_scan = walk_paths(directory, recursive=False)
            parent, _, name = query.rpartition("/")
            deadline = time.monotonic() + self.scan_seconds
            assert self._directory_scan is not None
            for _ in range(self.max_entries):
                if time.monotonic() >= deadline or not self._current(prefix):
                    break
                try:
                    candidate = next(self._directory_scan)
                except StopIteration:
                    self._directory_done = True
                    break
                if candidate is not None and candidate.lower().startswith(name.lower()):
                    self._rank(1, candidate)
            self._next_background = not self._directory_done
            self._next_status = (
                "Scanning directory…" if self._next_background else "Directory paths · explicit (ignores bypassed)"
            )
            paths = [parent + "/" + path for _score, path in self._best]
        else:
            self._next_background = False
            self._next_status = ""
            return ()
        return tuple(CompletionItem(marker + _quote(path), detail="directory" if path.endswith("/") else "file")
                     for path in paths)
