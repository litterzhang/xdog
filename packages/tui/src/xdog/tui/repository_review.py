"""Owned, read-only Git inspection with disk-paged patches and immutable views."""
from __future__ import annotations

import codecs
import hashlib
import os
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO, Literal

from xdog.tui.utils import sanitize_terminal_text, slice_by_column, strip_ansi

ReviewLane = Literal["working", "staged"]


def display_path(path: str) -> str:
    return "".join(
        f"\\u{ord(char):04x}" if unicodedata.category(char) in {"Cc", "Cs"}
        or (unicodedata.category(char) == "Cf" and char not in {"\u200c", "\u200d"}) else char
        for char in path
    )


def display_line(text: str) -> str:
    """Keep joined graphemes, remove terminal/bidi controls, expand tabs consistently."""
    return "".join(
        char for char in strip_ansi(text)
        if char == "\t" or char in {"\u200c", "\u200d"} or not unicodedata.category(char).startswith("C")
    ).expandtabs(4)


@dataclass(frozen=True, slots=True)
class ReviewFile:
    key: str
    path: str
    status: str
    original: str = ""
    repository: bool = False
    display_name: str = ""
    search_name: str = ""

    @property
    def label(self) -> str:
        return self.display_name or display_path(self.path)


@dataclass(frozen=True, slots=True)
class ReviewSnapshot:
    version: int = 0
    root: str = ""
    files: tuple[ReviewFile, ...] = ()
    key: str = ""
    lane: ReviewLane = "working"
    start: int = 0
    column: int = 0
    width: int = 0
    rows: tuple[str, ...] = ()
    total: int = 0
    hunks: tuple[int, ...] = ()
    busy: bool = False
    error: str = ""
    nested: bool = False
    operation: str = ""


@dataclass(frozen=True, slots=True)
class _Request:
    serial: int
    action: str
    key: str = ""
    lane: ReviewLane = "working"
    start: int = 0
    column: int = 0
    width: int = 80


class _Superseded(Exception):
    pass


class RepositoryReview:
    """One worker owns Git children, file reads, patch spool and SQLite pages.

    There are deliberately no stage/apply/revert/commit APIs. Git external diff,
    textconv, pager and fsmonitor commands are disabled. Repository metadata and
    hooks are never intentionally written/executed by inspection.
    """

    def __init__(self, workspace: str | Path, *, timeout: float = 20) -> None:
        self.workspace = Path(os.path.abspath(os.path.expanduser(str(workspace))))
        self.timeout = timeout
        self._condition = threading.Condition()
        self._snapshot = ReviewSnapshot()
        self._pending: _Request | None = None
        self._serial = 0
        self._closed = False
        self._thread: threading.Thread | None = None
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._directory: Path | None = None
        self._db: sqlite3.Connection | None = None
        self._root = self.workspace
        self._scope_stack: list[Path] = []
        self._files: dict[str, ReviewFile] = {}
        self._document: tuple[str, ReviewLane] | None = None
        self._total = 0
        self._hunks: tuple[int, ...] = ()
        self._process: subprocess.Popen[bytes] | None = None

    def snapshot(self) -> ReviewSnapshot:
        with self._condition:
            return self._snapshot

    def _request(self, action: str, key: str = "", lane: ReviewLane = "working", start: int = 0,
                 column: int = 0, width: int = 80) -> None:
        with self._condition:
            if self._closed:
                return
            self._serial += 1
            self._pending = _Request(self._serial, action, key, lane, max(0, start), max(0, column), max(1, width))
            self._snapshot = replace(
                self._snapshot, version=self._snapshot.version + 1, busy=True, error="", operation=action,
            )
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, name="xdog-repository-review", daemon=True)
                self._thread.start()
            self._condition.notify()

    def refresh(self) -> None:
        self._request("refresh")

    def page(self, key: str, lane: ReviewLane, start: int, column: int, width: int) -> None:
        self._request("page", key, lane, start, column, width)

    def enter(self, key: str) -> None:
        self._request("enter", key)

    def back(self) -> None:
        self._request("back")

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify()

    def _check(self, request: _Request) -> None:
        with self._condition:
            if self._closed or request.serial != self._serial:
                raise _Superseded

    def _environment(self) -> dict[str, str]:
        env = dict(os.environ)
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS"):
            env.pop(key, None)
        env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_LITERAL_PATHSPECS="1")
        return env

    def _git(self, request: _Request, args: list[str], cwd: Path | None = None) -> Path:
        assert self._directory is not None
        self._check(request)
        output = self._directory / "git-output"
        error = self._directory / "git-error"
        out_fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        err_fd = os.open(error, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(out_fd, "wb") as out, os.fdopen(err_fd, "wb") as err:
            self._process = subprocess.Popen(
                ["git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                 "-c", "core.hooksPath=", "-c", "diff.external=", *args],
                cwd=cwd or self._root, env=self._environment(), stdin=subprocess.DEVNULL, stdout=out, stderr=err,
            )
        deadline = time.monotonic() + self.timeout
        try:
            while self._process.poll() is None:
                self._check(request)
                if time.monotonic() >= deadline:
                    raise TimeoutError("Git review timed out; refresh to retry")
                with self._condition:
                    self._condition.wait(timeout=0.01)
            if self._process.returncode != 0:
                with error.open("rb") as stream:
                    detail = stream.read(2048).decode("utf-8", errors="replace")
                raise OSError(sanitize_terminal_text(detail).strip() or "Git review failed")
        finally:
            self._stop_process()
        return output

    def _stop_process(self) -> None:
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            process.wait(timeout=0.5)

    def _records(self, stream: BinaryIO, request: _Request) -> Iterator[bytes]:
        buffer = bytearray()
        while data := stream.read(65_536):
            self._check(request)
            buffer.extend(data)
            while (end := buffer.find(b"\0")) >= 0:
                yield bytes(buffer[:end])
                del buffer[:end + 1]
            if len(buffer) > 1024 * 1024:
                raise ValueError("Oversized Git status path record")
        if buffer:
            raise ValueError("Incomplete Git status record")

    def _refresh(self, request: _Request) -> ReviewSnapshot:
        root_output = self._git(request, ["rev-parse", "--show-toplevel"], self._root)
        with root_output.open("rb") as stream:
            raw_root = stream.read(65_537).removesuffix(b"\n")
        if len(raw_root) > 65_536 or not raw_root or b"\0" in raw_root:
            raise ValueError("Invalid repository root response")
        self._root = Path(os.fsdecode(raw_root))
        if not self._root.is_absolute():
            raise ValueError("Git returned a non-absolute repository root")
        output = self._git(request, ["status", "--porcelain=v1", "-z", "--untracked-files=all",
                                     "--ignore-submodules=none"])
        files: list[ReviewFile] = []
        with output.open("rb") as stream:
            records = iter(self._records(stream, request))
            for record in records:
                if len(record) < 4 or record[2:3] != b" ":
                    raise ValueError("Malformed Git status entry")
                status = record[:2].decode("ascii")
                raw_path = record[3:]
                original = os.fsdecode(next(records)) if "R" in status or "C" in status else ""
                path = os.fsdecode(raw_path)
                relative = Path(path)
                if relative.is_absolute() or ".." in relative.parts or (
                    original and (Path(original).is_absolute() or ".." in Path(original).parts)
                ):
                    raise ValueError("Git returned a path outside the repository")
                absolute = self._root / relative
                if self._directory is not None and absolute.is_relative_to(self._directory):
                    continue  # Do not review the private cache if TMPDIR is the workspace.
                key = hashlib.sha256(os.fsencode(self._root) + b"\0" + raw_path).hexdigest()
                nested = (absolute / ".git").exists() and absolute.is_dir() and not absolute.is_symlink()
                label = display_path(path)
                files.append(ReviewFile(key, path, status, original, nested, label, label.casefold()))
        files.sort(key=lambda item: (item.label.casefold(), item.key))
        self._files = {item.key: item for item in files}
        self._document = None
        return ReviewSnapshot(root=display_path(str(self._root)), files=tuple(files), nested=bool(self._scope_stack))

    def _line(self, text: str) -> None:
        assert self._db is not None
        self._db.execute("INSERT INTO lines(row, text) VALUES (?, ?)", (self._total, text))
        self._total += 1

    def _load_patch(self, request: _Request, file: ReviewFile) -> None:
        assert self._db is not None
        self._document = None
        self._db.execute("DELETE FROM lines")
        self._total = 0
        self._hunks = ()
        hunks: list[int] = []
        if file.status == "??" and request.lane == "staged":
            self._line("Untracked file is not in the index · w for working preview")
        elif file.status == "??":
            path = self._root / file.path
            mode = path.lstat().st_mode
            self._line(f"Untracked: {file.label} (preview only; not staged)")
            if stat.S_ISLNK(mode):
                self._line("Symlink target: " + display_path(os.readlink(path)))
            elif not stat.S_ISREG(mode):
                self._line("Not a regular file; contents not read")
            else:
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                with os.fdopen(fd, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError("File changed to a non-regular entry")
                    self._read_patch_lines(stream, request, hunks, untracked=True)
        else:
            output = self._git(request, ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--unified=3",
                                         "--submodule=short", *( ["--cached"] if request.lane == "staged" else []),
                                         "--", file.path, *([file.original] if file.original else [])])
            with output.open("rb") as stream:
                self._read_patch_lines(stream, request, hunks)
        if self._total == 0:
            self._line("No patch in this lane · s staged / w working · r refresh")
        self._db.commit()
        self._hunks = tuple(hunks)
        self._document = (file.key, request.lane)

    def _read_patch_lines(
        self, stream: BinaryIO, request: _Request, hunks: list[int], *, untracked: bool = False,
    ) -> None:
        continued = False
        if untracked:
            hunks.append(self._total)
            self._line("@@ untracked file preview @@")
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        while data := stream.readline(65_536):
            self._check(request)
            if untracked and b"\0" in data:
                assert self._db is not None
                self._db.execute("DELETE FROM lines")
                self._total = 0
                hunks.clear()
                self._line("Untracked binary file · contents not displayed")
                return
            text = display_line(decoder.decode(data, final=data.endswith(b"\n")).removesuffix("\n"))
            if not untracked and not continued and text.startswith("@@"):
                hunks.append(self._total)
            if continued:
                text = "↪ " + text
            if untracked:
                text = "+" + text
            self._line(text)
            continued = not data.endswith(b"\n")
        tail = decoder.decode(b"", final=True)
        if tail:
            self._line(("+" if untracked else "") + "↪ " + display_line(tail))

    def _page(self, request: _Request) -> ReviewSnapshot:
        assert self._db is not None
        file = self._files.get(request.key)
        if file is None:
            raise ValueError("File no longer listed · r refresh")
        if self._document != (file.key, request.lane):
            self._load_patch(request, file)
        start = min(request.start, max(0, self._total - 1))
        rows = self._db.execute("SELECT text FROM lines WHERE row >= ? ORDER BY row LIMIT 64", (start,)).fetchall()
        visible = tuple(slice_by_column(row[0], request.column, request.column + request.width) for row in rows)
        base = self.snapshot()
        return replace(base, key=file.key, lane=request.lane, start=start, column=request.column,
                       width=request.width, rows=visible, total=self._total, hunks=self._hunks)

    def _work(self) -> None:
        try:
            self._temporary = tempfile.TemporaryDirectory(prefix=".xdog-review-")
            self._directory = Path(self._temporary.name)
            db_path = self._directory / "patch.sqlite"
            os.close(os.open(db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            self._db = sqlite3.connect(db_path)
            self._db.execute("CREATE TABLE lines (row INTEGER PRIMARY KEY, text TEXT)")
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or self._pending is not None)
                    if self._closed:
                        return
                    request, self._pending = self._pending, None
                assert request is not None
                previous_root, previous_stack, previous_files = self._root, list(self._scope_stack), self._files
                try:
                    if request.action == "enter":
                        file = self._files.get(request.key)
                        if file is None or not file.repository:
                            raise ValueError("Selected entry is not an initialized nested repository")
                        self._scope_stack.append(self._root)
                        self._root /= file.path
                    elif request.action == "back":
                        if self._scope_stack:
                            self._root = self._scope_stack.pop()
                    result = self._page(request) if request.action == "page" else self._refresh(request)
                    result = replace(result, busy=False, error="", operation="")
                except _Superseded:
                    self._root, self._scope_stack, self._files = previous_root, previous_stack, previous_files
                    self._document = None
                    continue
                except (OSError, ValueError, sqlite3.Error, StopIteration) as exc:
                    self._root, self._scope_stack, self._files = previous_root, previous_stack, previous_files
                    self._document = None
                    result = replace(
                        self.snapshot(), busy=False, operation="", error=str(exc) or "Malformed review data",
                    )
                with self._condition:
                    if not self._closed and request.serial == self._serial:
                        self._snapshot = replace(result, version=self._snapshot.version + 1)
                    else:
                        self._root, self._scope_stack, self._files = previous_root, previous_stack, previous_files
                        self._document = None
        except (OSError, sqlite3.Error) as exc:
            with self._condition:
                self._snapshot = replace(self._snapshot, version=self._snapshot.version + 1,
                                         busy=False, error=f"Review unavailable: {exc}")
        finally:
            self._stop_process()
            if self._db is not None:
                self._db.close()
            if self._temporary is not None:
                self._temporary.cleanup()
