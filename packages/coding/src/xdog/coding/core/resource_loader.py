"""Load supported local project instructions; placement remains host-owned."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

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
    """Load only this directory's AGENTS.md; never walk parents or follow links.

    A missing file is optional. Invalid existing instructions fail explicitly
    rather than silently dropping a project constraint. Other files are not read.
    """
    try:
        root = os.open(working_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ProjectContextUnavailable('Project context directory is unavailable') from exc
    try:
        try:
            descriptor = os.open('AGENTS.md', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
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
            return []
        except (OSError, ValueError) as exc:
            raise ProjectContextUnavailable(
                'Project instruction unavailable: AGENTS.md; '
                'non-symlink regular UTF-8 files <=64 KiB required') from exc
    finally:
        os.close(root)
    return [ContextFile(path=working_dir / 'AGENTS.md', content=content, relative_path='AGENTS.md')]
