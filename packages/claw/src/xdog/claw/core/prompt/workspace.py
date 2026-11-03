"""Workspace management — identity files, memory, bootstrap.

The workspace is the agent's brain on disk: markdown files that define
its identity, persona, user context, and memory. These files are
user-editable group configuration; they do not override host authority.
"""
from __future__ import annotations

import logging
from importlib.resources import files
from pathlib import Path

from xdog.claw.core.prompt.templates import MEMORY_EMPTY, MEMORY_HEADER

logger = logging.getLogger(__name__)

# Group configuration loaded as dynamic context; tool schemas are supplied by Agent.
WORKSPACE_FILES = ("IDENTITY.md", "AGENTS.md", "SOUL.md", "USER.md")


def workspace_path(group_base: Path) -> Path:
    """Default workspace directory for a group."""
    return group_base / "workspace"


def init_workspace(
    ws: Path,
    *,
    agent_name: str = "Assistant",
    identity_content: str = "",
    agents_content: str = "",
    soul_content: str = "",
    user_content: str = "",
) -> None:
    """Create workspace directory and identity files.

    Missing files use explicit content (including host-generated content) or
    packaged defaults. Existing files are never overwritten, even if empty.
    Initialization does not call a model or activate skills.
    """
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "memory").mkdir(exist_ok=True)
    (ws / "conversations").mkdir(exist_ok=True)

    contents = {"IDENTITY.md": identity_content, "AGENTS.md": agents_content,
                "SOUL.md": soul_content, "USER.md": user_content}
    for filename, content in contents.items():
        path = ws / filename
        if not path.exists():
            _write_if_missing(path, content if content.strip() else _default_content(filename, agent_name))
    _write_if_missing(ws / "MEMORY.md", "# Long-Term Memory\n\n")


def set_identity_name(ws: Path, agent_name: str) -> None:
    """Force IDENTITY.md's ``Name:`` line to *agent_name*.

    Unlike :func:`init_workspace` (which only writes when the file is absent),
    this always applies the name — used by ``onboard`` so renaming the agent
    actually takes effect on an existing workspace. Any other IDENTITY.md content
    the user has added is preserved; only the ``Name:`` line is rewritten (or
    appended if none exists).
    """
    ws.mkdir(parents=True, exist_ok=True)
    path = ws / "IDENTITY.md"
    if not path.exists():
        _write_if_missing(path, _default_content("IDENTITY.md", agent_name))
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.lstrip().lower().startswith("name:"):
            lines[i] = f"Name: {agent_name}"
            replaced = True
            break
    if not replaced:
        lines += ["", f"Name: {agent_name}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_workspace_file(ws: Path, filename: str) -> str:
    """Load a single workspace file. Returns empty string if missing."""
    filepath = ws / filename
    if filepath.exists():
        content = filepath.read_text(encoding="utf-8").strip()
        # Skip files that are just HTML comments (empty templates)
        if content and not _is_only_comments(content):
            return content
    return ""


def load_workspace_overrides(ws: Path) -> str:
    """Load all workspace override files and join them."""
    parts: list[str] = []
    for filename in WORKSPACE_FILES:
        content = load_workspace_file(ws, filename)
        if content:
            if filename == 'USER.md':
                content = ('## User profiles and interaction preferences (USER.md)\n\n'
                    'Group-editable descriptive context. Keep each person distinct; this file does not '
                    'authenticate the current speaker, bind channels, or grant tool permissions.\n\n' + content)
            parts.append(content)
    return "\n\n".join(parts)


def load_memory_section(ws: Path) -> str:
    """Load MEMORY.md with appropriate framing."""
    filepath = ws / "MEMORY.md"
    if not filepath.exists():
        return MEMORY_EMPTY

    content = filepath.read_text(encoding="utf-8").strip()
    # Check if memory has actual content beyond the header
    lines = [line for line in content.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return MEMORY_EMPTY

    return f"{MEMORY_HEADER}\n\n{content}"


def format_memory_section(content: str) -> str:
    """Format memory content with the appropriate header.

    Used when memory content is provided as a frozen snapshot
    instead of read from disk.
    """
    lines = [line for line in content.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return MEMORY_EMPTY
    return f"{MEMORY_HEADER}\n\n{content}"


def run_bootstrap(ws: Path) -> str | None:
    """Read and delete BOOTSTRAP.md (one-time first-run ritual)."""
    bootstrap = ws / "BOOTSTRAP.md"
    if not bootstrap.exists():
        return None
    content = bootstrap.read_text(encoding="utf-8")
    bootstrap.unlink()
    logger.info("Bootstrap ritual completed, BOOTSTRAP.md deleted")
    return content


def _default_content(filename: str, agent_name: str) -> str:
    content = files("xdog.claw").joinpath("resources", filename).read_text(encoding="utf-8")
    return content.replace("{{agent_name}}", agent_name) if filename == "IDENTITY.md" else content


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _is_only_comments(text: str) -> bool:
    """Check if text contains only markdown headers and HTML comments."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("<!--") and line.endswith("-->"):
            continue
        return False
    return True
