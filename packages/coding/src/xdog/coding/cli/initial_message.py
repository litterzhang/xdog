"""Initial message builder: construct the first message from CLI args and piped stdin."""

from __future__ import annotations

import sys
from pathlib import Path

from xdog.ai.types import ImageContent
from xdog.coding.cli.file_processor import process_files


def build_initial_message(
    *,
    prompt: str | None = None,
    files: tuple[Path, ...] = (),
    read_stdin: bool = False,
    images: list[ImageContent] | None = None,
) -> str | None:
    """Build the initial user message from CLI arguments.

    Combines prompt text, file contents, and piped stdin into
    a single message string. Returns None if no input is provided.
    """
    parts: list[str] = []

    # Read piped stdin
    if read_stdin and not sys.stdin.isatty():
        try:
            stdin_content = sys.stdin.read()
            if stdin_content.strip():
                parts.append(stdin_content.strip())
        except Exception:
            pass

    # Process file arguments
    if files:
        file_entries = process_files(files)
        for entry in file_entries:
            etype = entry.get("type", "")
            path = entry.get("path", "")
            if etype == "file_content":
                content = entry.get("content", "")
                parts.append(f"File: {path}\n```\n{content}\n```")
            elif etype == "image":
                if images is None:
                    parts.append(f"[Image: {path}]")  # Compatibility for text-only external callers.
                else:
                    from xdog.ai.utils.image_files import read_reference
                    images.append(read_reference(Path(path)))
            elif etype == "file_reference":
                note = entry.get("note", "")
                parts.append(f"File: {path} ({note})")

    # Add the explicit prompt last
    if prompt:
        parts.append(prompt)

    if not parts:
        return None

    return "\n\n".join(parts)
