"""Resource loader: discover and load project context files and bundled assets.

Scans for project-level context files (AGENTS.md and .coding/ instructions)
and loads bundled prompt fragments from the package resources directory.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


@dataclass
class ProjectResources:
    """All discovered resources for a project."""

    context_files: list[ContextFile] = field(default_factory=list)
    custom_instructions: str = ""

    def to_file_entries(self) -> list[dict[str, Any]]:
        """Convert context files to file entry dicts for the system prompt."""
        entries: list[dict[str, Any]] = []
        for cf in self.context_files:
            entries.append({
                "type": "file_content",
                "path": cf.relative_path,
                "content": cf.content,
            })
        return entries


def get_package_resource_dir() -> Path:
    """Return the path to the package's bundled resource directory.

    Falls back to the package source directory itself if no dedicated
    ``resources`` sub-directory exists.
    """
    pkg_dir = Path(__file__).resolve().parent
    res_dir = pkg_dir / "resources"
    return res_dir if res_dir.is_dir() else pkg_dir


def load_text_resource(name: str) -> str:
    """Load a text file from the resource directory.

    Returns an empty string if the file does not exist.
    """
    path = get_package_resource_dir() / name
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def load_prompt_fragment(name: str) -> str:
    """Load a prompt fragment by name (e.g. ``"tools_header.txt"``)."""
    return load_text_resource(f"prompts/{name}")


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


def load_project_resources(working_dir: Path) -> ProjectResources:
    """Load all project resources from the working directory.

    Discovers context files, custom instructions, and other
    project-level configuration.
    """
    context_files = discover_context_files(working_dir)

    # Extract custom instructions from .coding/instructions files
    custom_parts: list[str] = []
    for cf in context_files:
        if "instructions" in cf.relative_path.lower():
            custom_parts.append(cf.content)

    return ProjectResources(
        context_files=context_files,
        custom_instructions="\n\n".join(custom_parts),
    )


def reload_resources(working_dir: Path) -> ProjectResources:
    """Re-scan and reload project resources.

    Alias for load_project_resources, but makes the intent explicit
    for callers that need to refresh on demand.
    """
    return load_project_resources(working_dir)
