"""External draft editing while the host has released terminal ownership."""
from __future__ import annotations

import os
import shlex
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_MAX_DRAFT_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ExternalEditorResult:
    text: str | None = None
    message: str = ""


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        pass  # The child can exit between poll and signal delivery.
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait(timeout=1)


def edit_draft(
    text: str, *, command: str | None = None, cwd: str | Path | None = None,
    stopped: Callable[[], bool] = lambda: False,
) -> ExternalEditorResult:
    """Run an explicitly user-selected editor; never interpret a shell command.

    Nonzero exit, interruption, missing/invalid output, or excessive size keeps
    the caller's original text and cursor. The private draft is removed on every
    path. Live hosts use user preferences, not model/extension-provided commands;
    no draft contents are logged.
    """
    process: subprocess.Popen[bytes] | None = None
    try:
        editor = command if command is not None else os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        argv = shlex.split(editor)
        if not argv:
            raise ValueError("editor command is empty")
        data = text.encode("utf-8")
        if len(data) > _MAX_DRAFT_BYTES:
            raise ValueError("draft exceeds 1 MiB")
        with tempfile.TemporaryDirectory(prefix="xdog-editor-") as directory:
            path = Path(directory) / "draft.txt"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            if stopped():
                return ExternalEditorResult(message="External editor canceled; draft preserved")
            try:
                process = subprocess.Popen([*argv, str(path)], cwd=cwd)
                while True:
                    if stopped():
                        _terminate(process)
                        return ExternalEditorResult(message="External editor canceled; draft preserved")
                    try:
                        code = process.wait(timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                if code != 0:
                    return ExternalEditorResult(message=f"External editor exited with status {code}; draft preserved")
                # Do not follow replacement symlinks or block on a replacement FIFO.
                if path.is_symlink():
                    raise ValueError("edited draft is a symlink")
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                with os.fdopen(fd, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError("edited draft is not a regular file")
                    edited = stream.read(_MAX_DRAFT_BYTES + 1)
                if len(edited) > _MAX_DRAFT_BYTES:
                    raise ValueError("edited draft exceeds 1 MiB")
                return ExternalEditorResult(edited.decode("utf-8"), "External editor returned; draft ready (not sent)")
            finally:
                if process is not None and process.poll() is None:
                    _terminate(process)

    except KeyboardInterrupt:
        return ExternalEditorResult(message="External editor interrupted; draft preserved")
    except (OSError, ValueError, UnicodeError) as exc:
        return ExternalEditorResult(message=f"External editor failed: {exc}; draft preserved")
