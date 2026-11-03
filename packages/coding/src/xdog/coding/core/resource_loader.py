"""Load supported local project instructions; placement remains host-owned."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

# Context file names to discover (in priority order)
CONTEXT_FILE_NAMES = (
    "AGENTS.md",
    ".coding/INSTRUCTIONS.md",
    ".coding/instructions.md",
)

# Max file size for context files (64KB)
MAX_CONTEXT_FILE_SIZE = 64 * 1024


@dataclass(frozen=True)
class ContextFile:
    """A discovered project context file."""

    path: Path
    content: str
    relative_path: str


class ProjectContextUnavailable(ValueError):
    """An existing instruction source must not silently disappear from context."""


def discover_context_files(working_dir: Path) -> list[ContextFile]:
    """Load supported local instructions only; no parent walk or symlink reads.

    Missing files are optional. Existing invalid/oversized files fail explicitly,
    because silently omitting an instruction could remove a project constraint.
    """
    found: list[ContextFile] = []
    try:
        root = os.open(working_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ProjectContextUnavailable('Project context directory is unavailable') from exc
    try:
        for name in CONTEXT_FILE_NAMES:
            parent = None
            try:
                relative = Path(name)
                if relative.parent != Path('.'):
                    parent = os.open(relative.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=root)
                descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=root if parent is None else parent)
                with os.fdopen(descriptor, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError('Instruction source is not a regular file')
                    if info.st_size > MAX_CONTEXT_FILE_SIZE:
                        raise ValueError('Instruction source exceeds 64 KiB')
                    raw = stream.read(MAX_CONTEXT_FILE_SIZE + 1)
                    if len(raw) > MAX_CONTEXT_FILE_SIZE:
                        raise ValueError('Instruction source grew beyond 64 KiB')
                    content = raw.decode('utf-8')
            except FileNotFoundError:
                continue  # Missing sources are optional; no-follow rejects dangling symlinks separately.
            except (OSError, ValueError) as exc:
                raise ProjectContextUnavailable(
                    f'Project instruction unavailable: {name}; '
                    'non-symlink regular UTF-8 files <=64 KiB required') from exc
            finally:
                if parent is not None:
                    os.close(parent)
            found.append(ContextFile(path=working_dir / name, content=content, relative_path=name))
    finally:
        os.close(root)
    return found
