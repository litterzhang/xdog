"""Transcript compaction — cut, summarize, and archive.

Pure functions for estimating tokens, finding cut points, extracting
file operations, compacting transcripts, and archiving to markdown.
"""
from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from xdog.claw.core.persistence.transcript_convert import TOOL_RESULT_ROLE, entry_text, entry_tool_calls

_PREVIOUS_SUMMARY_TAG = "previous-summary"

_PREVIOUS_SUMMARY_RE = re.compile(
    r"<previous-summary>\s*(.*?)\s*</previous-summary>",
    re.DOTALL,
)


def extract_previous_summary(transcript: list[dict[str, Any]]) -> str | None:
    """Scan transcript for the most recent ``<previous-summary>`` block."""
    for turn in reversed(transcript):
        if not turn.get("is_compaction"):
            continue
        content = entry_text(turn)
        m = _PREVIOUS_SUMMARY_RE.search(content)
        if m:
            return m.group(1).strip()
    return None


def estimate_tokens(turns: list[dict[str, Any]]) -> int:
    """Rough token estimate: ~4 chars per token.

    Counts both ``content`` and ``tool_calls`` argument payloads.
    """
    total_chars = 0
    for t in turns:
        total_chars += len(str(t.get("content", "")))
        for tc in t.get("tool_calls", []):
            args = tc.get("arguments", {})
            total_chars += len(str(args))
    return total_chars // 4


def find_cut_point(turns: list[dict[str, Any]], target_tokens: int) -> int:
    """Walk backwards from end, summing tokens until *target_tokens* is reached.

    Uses the same counting logic as ``estimate_tokens`` (content + tool args).
    Returns the index where recent turns begin (everything before is compacted).
    """
    running = 0
    for idx in range(len(turns) - 1, -1, -1):
        t = turns[idx]
        running += len(str(t.get("content", ""))) // 4
        for tc in t.get("tool_calls", []):
            running += len(str(tc.get("arguments", {}))) // 4
        if running >= target_tokens:
            return idx
    return 0


def extract_file_ops(turns: list[dict[str, Any]]) -> str:
    """Scan turns for filesystem tool calls and return a markdown snippet.

    Matches the current ``filesystem`` tool with ``action`` parameter:
    ``read``, ``write``, ``edit``, ``delete``.
    """
    read_paths: set[str] = set()
    write_paths: set[str] = set()

    for turn in turns:
        if turn.get("role") != "assistant":
            continue
        for tc in entry_tool_calls(turn):
            name = tc.get("name", "")
            args = tc.get("arguments", {})
            if isinstance(args, str):
                continue
            if name != "filesystem":
                continue
            path = args.get("path", "")
            if not path:
                continue
            action = args.get("action", "")
            if action == "read":
                read_paths.add(path)
            elif action in ("write", "edit", "delete"):
                write_paths.add(path)

    if not read_paths and not write_paths:
        return ""

    lines = ["\n# Files"]
    if read_paths:
        lines.append("Read: " + ", ".join(sorted(read_paths)))
    if write_paths:
        lines.append("Modified: " + ", ".join(sorted(write_paths)))
    return "\n".join(lines)


def compact_transcript(
    turns: list[dict[str, Any]],
    *,
    summary: str,
    target_tokens: int = 150_000,
    first_kept_index: int | None = None,
) -> list[dict[str, Any]]:
    """Replace old turns with a summary, keeping recent turns intact.

    Ensures the kept portion starts at a valid message boundary:
    - Never starts with a ``tool`` result (orphaned tool_result)
    - Never starts mid-way through an assistant+tool_results sequence
    """
    cut = find_cut_point(turns, target_tokens) if first_kept_index is None else first_kept_index
    if (first_kept_index is not None and (type(cut) is not int or not 1 <= cut < len(turns)
                                         or turns[cut].get('role') != 'user')):
        raise ValueError('Compaction must retain a complete user turn')
    if cut == 0:
        return list(turns)

    # Boundary fix: walk forward to a valid start
    while cut < len(turns):
        role = turns[cut].get("role", "")
        if role == TOOL_RESULT_ROLE:
            cut += 1
        elif role == "assistant" and entry_tool_calls(turns[cut]):
            cut += 1
            while cut < len(turns) and turns[cut].get("role") == TOOL_RESULT_ROLE:
                cut += 1
        else:
            break

    recent = turns[cut:]
    if not recent:
        recent = [
            t for t in turns
            if t.get("role") in ("user", "system")
            or (t.get("role") == "assistant" and not t.get("tool_calls"))
        ][-10:]

    # Append file-ops from compacted turns
    compacted_turns = turns[:cut]
    file_ops = extract_file_ops(compacted_turns)
    full_summary = summary + file_ops if file_ops else summary

    compact_entry: dict[str, Any] = {
        "role": "system",
        "content": (
            f"<{_PREVIOUS_SUMMARY_TAG}>\n"
            f"{full_summary}\n"
            f"</{_PREVIOUS_SUMMARY_TAG}>"
        ),
        "timestamp": time.time(),
        "is_compaction": True,
    }
    return [compact_entry] + recent


def archive_transcript(
    turns: list[dict[str, Any]],
    conversations_dir: Path,
    *,
    topic: str = "conversation",
) -> Path:
    """Save full transcript as markdown to conversations directory."""
    conversations_dir.mkdir(parents=True, exist_ok=True)
    date_str = time.strftime("%Y-%m-%d")
    time_str = time.strftime("%H%M%S")
    safe_topic = (
        "".join(c if c.isalnum() or c in "-_ " else "" for c in topic)[:50].strip()
    )
    filename = f"{date_str}-{time_str}-{safe_topic or 'session'}-{uuid.uuid4().hex}.md"
    path = conversations_dir / filename

    lines = [f"# Conversation Archive \u2014 {date_str}\n"]
    for turn in turns:
        role = turn.get("role", "unknown")
        content = turn.get("content", "")
        lines.append(f"\n## {role.title()}\n\n{content}\n")

    # Full conversation archives are private and create-only, even when two
    # cheap extension-provided compactions finish in the same second.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    opened = False
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            opened = True
            output.write("\n".join(lines))
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if not opened:
            try:
                os.close(descriptor)
            except OSError:
                pass
        path.unlink(missing_ok=True)
        raise
    return path
