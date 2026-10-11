"""Worker-owned, resumable name indexing; never execute repository hooks.

Git supplies ignore semantics when available. A private temporary Git directory
provides the same rules for non-repositories without changing the workspace.
Without Git, an explicitly reported basic-exclusion walker remains available.
Names live in a private SQLite index, not an unbounded in-memory candidate list.
"""
from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import tempfile
import time
import unicodedata
from collections import deque
from collections.abc import Generator
from pathlib import Path, PurePosixPath
from typing import BinaryIO

EXCLUDED = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache",
})


def safe_path(path: str) -> bool:
    return not any(
        unicodedata.category(char) in {"Cc", "Cs"}
        or (unicodedata.category(char) == "Cf" and char not in {"\u200c", "\u200d"})
        for char in path
    )


def walk_paths(directory: Path, *, recursive: bool) -> Generator[str | None, None, None]:
    """Yield once per visited entry, including skipped ones, retaining scan state."""
    pending = deque([directory])
    while pending:
        folder = pending.popleft()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if not safe_path(entry.name) or (
                        recursive and (entry.name.startswith(".") or entry.name in EXCLUDED)
                    ):
                        yield None
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        yield None
                        continue
                    relative = Path(entry.path).relative_to(directory).as_posix()
                    yield relative + ("/" if is_dir else "")
                    if recursive and is_dir:
                        pending.append(Path(entry.path))
        except OSError:
            yield None


class _Waiting:
    pass


_WAIT = _Waiting()


class WorkspaceIndex:
    """All I/O and SQLite access belongs to the provider's one worker thread."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._temporary = tempfile.TemporaryDirectory(prefix=".xdog-file-index-")
        self.directory = Path(self._temporary.name)
        db_path = self.directory / "names.sqlite"
        try:
            os.close(os.open(db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            self._db = sqlite3.connect(db_path)
        except (OSError, sqlite3.Error):
            self._temporary.cleanup()
            raise
        try:
            self._db.execute("CREATE TABLE paths (path TEXT PRIMARY KEY)")
        except sqlite3.Error:
            self._db.close()
            self._temporary.cleanup()
            raise
        self._source: Generator[str | None | _Waiting, None, None] | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: BinaryIO | None = None
        self._started = 0.0
        self.generation = 0
        self.count = 0
        self.max_rowid = 0
        self.scanning = False
        self.mode = "Git ignore rules"
        self.error = ""
        self._bare_ready = False
        self._nested_roots: deque[PurePosixPath] = deque()
        self._visited_roots: set[PurePosixPath] = set()

    def begin(self) -> None:
        self._close_source()
        self._db.execute("DELETE FROM paths")
        self.generation += 1
        self.count = self.max_rowid = 0
        self._nested_roots.clear()
        self._visited_roots.clear()
        self.scanning = True
        self.mode = "Git ignore rules"
        self.error = ""
        self._source = self._git_paths()

    def _environment(self) -> dict[str, str]:
        env = dict(os.environ)
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
            env.pop(key, None)
        env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
        return env

    def _launch(self, args: list[str]) -> None:
        # Separate open descriptions: a reader seek must not move the child's
        # output offset. Regular-file spooling also avoids blocking pipe reads.
        path = self.directory / "git-output"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as writer:
            self._process = subprocess.Popen(
                ["git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                 "-c", "core.hooksPath=", *args], cwd=self.root, env=self._environment(),
                stdin=subprocess.DEVNULL, stdout=writer, stderr=subprocess.DEVNULL,
            )
        self._reader = path.open("rb")
        self._started = time.monotonic()

    def _records(self) -> Generator[str | _Waiting, None, int]:
        assert self._reader is not None and self._process is not None
        buffer = bytearray()
        while True:
            chunk = self._reader.read(65_536)
            if chunk:
                buffer.extend(chunk)
                if len(buffer) > 1024 * 1024:
                    raise OSError("Git returned oversized path records")
                while (end := buffer.find(b"\0")) >= 0:
                    record = bytes(buffer[:end])
                    del buffer[:end + 1]
                    yield os.fsdecode(record)
                continue
            code = self._process.poll()
            if code is not None:
                # A final read is needed if the child wrote between read/poll.
                chunk = self._reader.read(65_536)
                if chunk:
                    buffer.extend(chunk)
                    if len(buffer) > 1024 * 1024:
                        raise OSError("Git returned oversized path records")
                    while (end := buffer.find(b"\0")) >= 0:
                        record = bytes(buffer[:end])
                        del buffer[:end + 1]
                        yield os.fsdecode(record)
                    continue
                self._finish_process()
                return code
            if time.monotonic() - self._started > 30:
                raise OSError("Git enumeration exceeded 30 seconds; using basic exclusions")
            yield _WAIT

    def _arguments(self, *, bare: bool) -> list[str]:
        return [
            *([f"--git-dir={self.directory / 'git'}", f"--work-tree={self.root}"] if bare else []),
            "ls-files", "--cached", "--others", "--exclude-standard", "-z",
            "--exclude=.*", *(f"--exclude={name}/" for name in sorted(EXCLUDED)), "--", ".",
        ]

    def _nested_records(self, base: PurePosixPath) -> Generator[str | _Waiting, None, int]:
        records = self._records()
        try:
            while True:
                try:
                    path = next(records)
                except StopIteration as end:
                    return int(end.value)
                yield str(base / path) if isinstance(path, str) else path
        finally:
            records.close()

    def _git_paths(self) -> Generator[str | None | _Waiting, None, None]:
        try:
            self._launch(self._arguments(bare=False))
            code = yield from self._records()
            if code == 128:
                if not self._bare_ready:
                    self._launch(["init", "--bare", "--template=", "-q", str(self.directory / "git")])
                    if (yield from self._records()) != 0:
                        raise OSError("Cannot create private Git metadata")
                    self._bare_ready = True
                self._launch(self._arguments(bare=True))
                code = yield from self._records()
            if code:
                raise OSError(f"Git enumeration failed (status {code})")
            # Git reports submodules/nested repositories as directory entries.
            # Enumerate each real nested root with its own ignore rules, retaining
            # root-relative names and the same one-child ownership/cancellation.
            while self._nested_roots:
                nested = self._nested_roots.popleft()
                self._launch(["-C", str(self.root / nested), *self._arguments(bare=False)])
                nested_code = yield from self._nested_records(nested)
                if nested_code:
                    self.error = f"Nested repository {nested}: enumeration failed (status {nested_code})"
        except OSError as exc:
            self._finish_process()
            self.mode = "basic exclusions (Git ignores unavailable)"
            self.error = str(exc)
            yield from walk_paths(self.root, recursive=True)
        finally:
            self._finish_process()

    def advance(self, max_entries: int, seconds: float) -> None:
        if self._source is None or not self.scanning:
            return
        deadline = time.monotonic() + seconds
        for _ in range(max_entries):
            if time.monotonic() >= deadline:
                break
            try:
                path = next(self._source)
            except StopIteration:
                self.scanning = False
                break
            if isinstance(path, _Waiting):
                break
            if path is None or not safe_path(path):
                continue
            relative = PurePosixPath(path.rstrip("/"))
            if relative.is_absolute() or ".." in relative.parts:
                continue
            try:
                mode = (self.root / relative).lstat().st_mode
            except OSError:
                continue  # Deleted tracked names are not selectable files.
            if stat.S_ISDIR(mode):
                path = str(relative) + "/"
                if (
                    self.mode == "Git ignore rules" and relative not in self._visited_roots
                    and (self.root / relative / ".git").exists()
                ):
                    self._visited_roots.add(relative)
                    self._nested_roots.append(relative)
            self._insert(path)
            for parent in relative.parents:
                if str(parent) != ".":
                    self._insert(str(parent) + "/")
        self._db.commit()

    def _insert(self, path: str) -> None:
        cursor = self._db.execute("INSERT OR IGNORE INTO paths(path) VALUES (?)", (path,))
        if cursor.rowcount:
            self.count += 1
            self.max_rowid = cursor.lastrowid or self.max_rowid

    def rows_after(self, position: int, limit: int) -> list[tuple[int, str]]:
        return self._db.execute(
            "SELECT rowid, path FROM paths WHERE rowid > ? ORDER BY rowid LIMIT ?", (position, limit),
        ).fetchall()

    def _finish_process(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
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
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def _close_source(self) -> None:
        if self._source is not None:
            self._source.close()
            self._source = None
        self._finish_process()

    def close(self) -> None:
        self._close_source()
        self._db.close()
        self._temporary.cleanup()
