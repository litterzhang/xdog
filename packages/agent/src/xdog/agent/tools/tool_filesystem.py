"""Filesystem tool — read, write, delete, edit, ls, grep, find. Uses ToolDef framework."""
from __future__ import annotations

import asyncio
import base64
from _thread import LockType
from pathlib import Path
from threading import Lock
from typing import Any
from weakref import WeakValueDictionary

from xdog.agent.async_work import run_owned
from xdog.agent.core import AgentToolResult
from xdog.agent.tool_def import Param, ToolDef, action
from xdog.agent.tools._edit_utils import (
    apply_edits,
    count_occurrences,
    detect_line_ending,
    generate_diff_string,
    normalize_for_fuzzy,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)
from xdog.agent.tools._utils import (
    _DEFAULT_FIND_LIMIT,
    _DEFAULT_GREP_MATCH_LIMIT,
    _DEFAULT_LS_LIMIT,
    _DEFAULT_READ_LIMIT,
    _GREP_LINE_MAX_CHARS,
    _IMAGE_EXTENSIONS,
    _IMAGE_MAX_SIZE,
    _MAX_LINE_LENGTH,
    human_size,
    shell_quote,
    truncate,
    validate_path,
)
from xdog.ai.types import ImageContent, TextContent

# Shared across owners/threads, but never bound to an event loop. The short
# guard only protects lock lookup/creation; all waits and filesystem IO run in
# the transaction worker. Hold a strong reference while acquiring/using a lock
# so the weak cache cannot replace it while another transaction is waiting.
_file_locks: WeakValueDictionary[str, LockType] = WeakValueDictionary()
_file_locks_guard = Lock()


def _edit_lock(path: Path) -> LockType:
    key = str(path.resolve())
    with _file_locks_guard:
        lock = _file_locks.get(key)
        if lock is None:
            lock = Lock()
            _file_locks[key] = lock
        return lock


def _edit_transaction(path: str, ctx: dict[str, Any], args: dict[str, Any]) -> str | AgentToolResult:
    path = _resolve(path, ctx)
    error = validate_path(path, confine_to=_confinement(ctx))
    if error:
        return error
    target = Path(path)
    with _edit_lock(target):
        return _fs_edit(target, args)


#: Key a caller puts in ``tool_ctx`` to confine this tool to a set of roots.
#: Absent means unconfined, which is the historical behaviour and what
#: xdog-coding and xdog-claw rely on.
CONFINE_CTX_KEY = "fs_confine_to"

#: Key a caller puts in ``tool_ctx`` to give this tool a working directory.
#: A relative path then resolves inside it instead of being rejected. This is
#: independent of :data:`CONFINE_CTX_KEY`: a workspace says where "here" is, a
#: confinement says where the walls are, and a run can have the first without
#: the second.
WORKSPACE_CTX_KEY = "fs_workspace"


def _confinement(ctx: dict[str, Any]) -> list[Path] | None:
    """The roots this call may touch, or None when the caller set no bound.

    Absent and empty are deliberately different. Absent means "no bound" and
    allows everything the denylist permits. Empty means the caller computed a
    bound and it came out empty, which must close the door rather than open it —
    otherwise a bug upstream that produces no roots silently grants everything,
    which is the failure this whole feature exists to prevent.
    """
    roots = ctx.get(CONFINE_CTX_KEY)
    if roots is None:
        return None
    return [Path(r) for r in roots]


def _resolve(path: str, ctx: dict[str, Any]) -> str:
    """Make *path* absolute, relative to the caller's workspace when there is one.

    Without a workspace a relative path is left alone and ``validate_path``
    rejects it, which is the historical behaviour. With one, "notes.md" means
    the workspace's notes.md — so a model that reasons in relative paths, as
    they do, lands somewhere predictable rather than in whatever directory the
    process happened to start in.
    """
    workspace = ctx.get(WORKSPACE_CTX_KEY)
    if workspace is None or not path or Path(path).is_absolute():
        return path
    return str(Path(workspace) / path)


class FilesystemTool(ToolDef):
    name = "filesystem"
    description = (
        "File operations: read (with line numbers), write, delete, edit "
        "(find-and-replace), ls (list directory), grep (search contents), "
        "and find (search by filename)."
    )

    @action("read", description="Read a file with line numbers",
            path=Param("string", required=True, description="Absolute file path"),
            offset=Param("integer", description="Line number to start from (1-based)"),
            limit=Param("integer", description=f"Max lines to return. Default: {_DEFAULT_READ_LIMIT}"))
    async def read(self, ctx: dict[str, Any], path: str, offset: int = 1, limit: int = _DEFAULT_READ_LIMIT) -> str | AgentToolResult:
        path = _resolve(path, ctx)
        error = validate_path(path, confine_to=_confinement(ctx))
        if error:
            return error
        return _fs_read(Path(path), {"offset": offset, "limit": limit})

    @action("write", description="Write content to a file",
            path=Param("string", required=True, description="Absolute file path"),
            content=Param("string", required=True, description="Content to write"))
    async def write(self, ctx: dict[str, Any], path: str, content: str) -> str:
        path = _resolve(path, ctx)
        error = validate_path(path, confine_to=_confinement(ctx))
        if error:
            return error
        return _fs_write(Path(path), content)

    @action("delete", description="Delete a file",
            path=Param("string", required=True, description="Absolute file path"))
    async def delete(self, ctx: dict[str, Any], path: str) -> str:
        path = _resolve(path, ctx)
        error = validate_path(path, confine_to=_confinement(ctx))
        if error:
            return error
        return _fs_delete(Path(path))

    @action("edit", description="Find-and-replace text in a file",
            path=Param("string", required=True, description="Absolute file path"),
            old_string=Param("string", description="Text to find (single-edit mode)"),
            new_string=Param("string", description="Replacement text"),
            edits=Param("array", description="Multiple replacements", items={
                "type": "object", "properties": {"old_string": {"type": "string"}, "new_string": {"type": "string"}},
                "required": ["old_string", "new_string"],
            }),
            replace_all=Param("boolean", description="Replace all occurrences"))
    async def edit(self, ctx: dict[str, Any], path: str, old_string: str = "", new_string: str = "",
                   edits: list[Any] | None = None, replace_all: bool = False) -> str | AgentToolResult:
        # Once dispatched, settle the actual transaction even on repeated caller
        # cancellation. Never publish its late result or replay a possible write.
        return await run_owned(_edit_transaction, path, ctx, {
            "old_string": old_string, "new_string": new_string,
            "edits": edits, "replace_all": replace_all,
        })

    @action("ls", description="List directory contents",
            path=Param("string", required=True, description="Absolute directory path"),
            show_hidden=Param("boolean", description="Include hidden files"))
    async def ls(self, ctx: dict[str, Any], path: str, show_hidden: bool = False) -> str | AgentToolResult:
        path = _resolve(path, ctx)
        error = validate_path(path, confine_to=_confinement(ctx))
        if error:
            return error
        return _fs_ls(Path(path), {"show_hidden": show_hidden})

    @action("grep", description="Search file contents using ripgrep",
            path=Param("string", required=True, description="Directory or file to search"),
            pattern=Param("string", required=True, description="Regex pattern"),
            glob=Param("string", description="Glob filter (e.g. '*.py')"),
            output_mode=Param("string", enum=["content", "files_with_matches", "count"]),
            case_insensitive=Param("boolean"),
            context=Param("integer", description="Context lines"),
            multiline=Param("boolean"),
            head_limit=Param("integer"))
    async def grep(self, ctx: dict[str, Any], path: str, pattern: str, **kwargs: Any) -> str:
        path = _resolve(path, ctx)
        error = validate_path(path, confine_to=_confinement(ctx))
        if error:
            return error
        return await _fs_grep(Path(path), {"pattern": pattern, **kwargs}, cancel=ctx.get("_cancel"))

    @action("find", description="Find files by glob pattern",
            path=Param("string", required=True, description="Directory to search"),
            pattern=Param("string", required=True, description="Glob pattern"),
            head_limit=Param("integer"))
    async def find(self, ctx: dict[str, Any], path: str, pattern: str, head_limit: int = _DEFAULT_FIND_LIMIT) -> str:
        path = _resolve(path, ctx)
        error = validate_path(path, confine_to=_confinement(ctx))
        if error:
            return error
        return await _fs_find(Path(path), {"pattern": pattern, "head_limit": head_limit}, cancel=ctx.get("_cancel"))


def create_filesystem_tool() -> Any:
    return FilesystemTool().build()


# ---------------------------------------------------------------------------
# Internal helpers (unchanged from original)
# ---------------------------------------------------------------------------

def _fs_read(path: Path, args: dict[str, Any]) -> str | AgentToolResult:
    if not path.exists():
        return f"Error: file not found: {path}"
    if path.is_dir():
        return f"Error: {path} is a directory, not a file. Use action='ls' for directories."

    suffix = path.suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        return _fs_read_image(path)

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Error: cannot read {path} as text (binary file?)."

    lines = content.splitlines(keepends=True)
    total_lines = len(lines)

    offset = max(1, args.get("offset", 1) or 1)
    limit = args.get("limit", _DEFAULT_READ_LIMIT) or _DEFAULT_READ_LIMIT

    start = offset - 1
    end = start + limit
    selected = lines[start:end]

    numbered: list[str] = []
    for i, line in enumerate(selected, start=offset):
        display = line.rstrip("\n\r")
        if len(display) > _MAX_LINE_LENGTH:
            display = display[:_MAX_LINE_LENGTH] + "..."
        numbered.append(f"{i}\t{display}")

    result_text = "\n".join(numbered)

    if end < total_lines:
        remaining = total_lines - end
        result_text += f"\n\n({remaining} more lines. Use offset={end + 1} to continue reading)"

    return AgentToolResult(content=(TextContent(text=result_text),))


def _fs_read_image(path: Path) -> str | AgentToolResult:
    size = path.stat().st_size
    if size > _IMAGE_MAX_SIZE:
        return f"Error: image too large ({human_size(size)}). Max: {human_size(_IMAGE_MAX_SIZE)}."
    import mimetypes
    mime, _ = mimetypes.guess_type(str(path))
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return AgentToolResult(content=(ImageContent(data=data, mime_type=mime or "image/png"),))


def _fs_write(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    lines = content.count("\n") + (0 if content.endswith("\n") else 1)
    return f"Wrote {len(content)} chars ({lines} lines) to {path}"


def _fs_delete(path: Path) -> str:
    if not path.exists():
        return f"Error: file not found: {path}"
    if path.is_dir():
        return f"Error: {path} is a directory. Only files can be deleted."
    path.unlink()
    return f"Deleted {path}"


def _fs_edit(path: Path, args: dict[str, Any]) -> str | AgentToolResult:
    if not path.exists():
        return f"Error: file not found: {path}"
    if path.is_dir():
        return f"Error: {path} is a directory."

    raw_content = path.read_text(encoding="utf-8")
    bom, content = strip_bom(raw_content)
    original_ending = detect_line_ending(content)
    normalized_content = normalize_to_lf(content)

    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    edits = args.get("edits")
    replace_all = args.get("replace_all", False)

    try:
        if old_string and replace_all:
            occurrences = count_occurrences(normalized_content, normalize_to_lf(old_string))
            if occurrences == 0:
                raise ValueError(f"Could not find the text in {path}.")
            fuzzy_content = normalize_for_fuzzy(normalized_content)
            fuzzy_old = normalize_for_fuzzy(normalize_to_lf(old_string))
            fuzzy_new = normalize_to_lf(new_string)
            new_content = fuzzy_content.replace(fuzzy_old, fuzzy_new) if fuzzy_old != normalize_to_lf(old_string) else normalized_content.replace(normalize_to_lf(old_string), fuzzy_new)
            base_content = fuzzy_content if fuzzy_old != normalize_to_lf(old_string) else normalized_content

            diff_text, _ = generate_diff_string(base_content, new_content)

            final_content = bom + restore_line_endings(new_content, original_ending)
            path.write_text(final_content, encoding="utf-8")

            return AgentToolResult(content=(TextContent(text=f"Replaced {occurrences} occurrence(s).\n\n{diff_text}"),))

        elif old_string:
            edit_list = [{"old_string": old_string, "new_string": new_string}]
        elif edits:
            edit_list = edits
        else:
            return "Error: provide old_string/new_string or edits array."

        base_content, new_content = apply_edits(normalized_content, edit_list, str(path))

    except ValueError as exc:
        return f"Error: {exc}"

    diff_text, _ = generate_diff_string(base_content, new_content)

    final_content = bom + restore_line_endings(new_content, original_ending)
    path.write_text(final_content, encoding="utf-8")

    return AgentToolResult(content=(TextContent(text=diff_text),))


def _fs_ls(path: Path, args: dict[str, Any]) -> str | AgentToolResult:
    if not path.exists():
        return f"Error: directory not found: {path}"
    if not path.is_dir():
        return f"Error: {path} is not a directory."

    show_hidden = args.get("show_hidden", False)
    entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))

    lines: list[str] = []
    count = 0
    for entry in entries:
        if not show_hidden and entry.name.startswith("."):
            continue
        count += 1
        if count > _DEFAULT_LS_LIMIT:
            lines.append(f"\n... ({len(list(entries)) - _DEFAULT_LS_LIMIT} more entries)")
            break
        name = entry.name
        if entry.is_symlink():
            target = entry.resolve()
            if not target.exists():
                lines.append(f"  {name} -> (broken symlink)")
            else:
                lines.append(f"  {name} -> {target}")
        elif entry.is_dir():
            lines.append(f"  {name}/")
        else:
            try:
                size = entry.stat().st_size
                lines.append(f"  {name} ({human_size(size)})")
            except OSError:
                lines.append(f"  {name}")

    header = f"Directory: {path}\n"
    return AgentToolResult(content=(TextContent(text=truncate(header + "\n".join(lines))),))


async def _fs_grep(path: Path, args: dict[str, Any], cancel: Any = None) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        return "Error: pattern is required for grep."

    cmd_parts = ["rg", "--no-heading"]
    output_mode = args.get("output_mode", "files_with_matches")

    if output_mode == "files_with_matches":
        cmd_parts.append("-l")
    elif output_mode == "count":
        cmd_parts.append("-c")
    else:
        cmd_parts.append("-n")

    if args.get("case_insensitive"):
        cmd_parts.append("-i")
    if args.get("multiline"):
        cmd_parts.extend(["-U", "--multiline-dotall"])

    context = args.get("context")
    if context is not None:
        cmd_parts.extend(["-C", str(int(context))])

    glob_pattern = args.get("glob")
    if glob_pattern:
        cmd_parts.extend(["--glob", shell_quote(glob_pattern)])

    file_type = args.get("type")
    if file_type:
        cmd_parts.extend(["--type", file_type])

    cmd_parts.append("--")
    cmd_parts.append(shell_quote(pattern))
    cmd_parts.append(shell_quote(str(path)))

    command = " ".join(cmd_parts)

    head_limit = args.get("head_limit")
    effective_limit = int(head_limit) if head_limit is not None and int(head_limit) > 0 else _DEFAULT_GREP_MATCH_LIMIT
    if effective_limit > 0:
        command += f" | head -n {effective_limit}"

    cwd = str(path) if path.is_dir() else str(path.parent)

    try:
        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        output = stdout_bytes.decode("utf-8", errors="replace").rstrip()

        if proc.returncode == 127 or "command not found" in stderr_text:
            fallback_parts = ["grep", "-rn"]
            if args.get("case_insensitive"):
                fallback_parts.append("-i")
            if output_mode == "files_with_matches":
                fallback_parts.append("-l")
            elif output_mode == "count":
                fallback_parts.append("-c")
            if glob_pattern:
                fallback_parts.extend(["--include", shell_quote(glob_pattern)])
            fallback_parts.extend(["--", shell_quote(pattern), shell_quote(str(path))])
            fallback_cmd = " ".join(fallback_parts)
            if effective_limit > 0:
                fallback_cmd += f" | head -n {effective_limit}"
            proc2 = await asyncio.create_subprocess_shell(
                fallback_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd,
            )
            stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=30.0)
            output = stdout2.decode("utf-8", errors="replace").rstrip()

        if not output:
            return "No matches found."

        if output_mode == "content":
            lines = []
            for line in output.splitlines():
                lines.append(line[:_GREP_LINE_MAX_CHARS] + "..." if len(line) > _GREP_LINE_MAX_CHARS else line)
            output = "\n".join(lines)

        return truncate(output)

    except asyncio.TimeoutError:
        return "Search timed out after 30s."
    except Exception as exc:
        return f"Error: {exc}"


async def _fs_find(path: Path, args: dict[str, Any], cancel: Any = None) -> str:
    pattern = args.get("pattern", "")
    if not pattern:
        return "Error: pattern is required for find."

    search_path = str(path)
    cwd = search_path if path.is_dir() else str(path.parent)
    head_limit = args.get("head_limit", _DEFAULT_FIND_LIMIT)

    try:
        fd_cmd = f"fd --glob {shell_quote(pattern)} {shell_quote(search_path)} --type f"
        proc = await asyncio.create_subprocess_shell(
            fd_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        output = stdout_bytes.decode("utf-8", errors="replace").rstrip()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        if proc.returncode == 127 or "command not found" in stderr_text.lower() or (not output and proc.returncode != 0):
            find_cmd = f"find {shell_quote(search_path)} -type f -name {shell_quote(pattern)} 2>/dev/null | sort"
            proc2 = await asyncio.create_subprocess_shell(
                find_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd,
            )
            stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=30.0)
            output = stdout2.decode("utf-8", errors="replace").rstrip()

    except asyncio.TimeoutError:
        return "Search timed out after 30s."
    except Exception as exc:
        return f"Error: {exc}"

    if not output:
        return "No matching files found."

    lines = sorted(output.splitlines())
    effective_limit = int(head_limit) if head_limit is not None and int(head_limit) > 0 else _DEFAULT_FIND_LIMIT
    if len(lines) > effective_limit:
        total = len(lines)
        lines = lines[:effective_limit]
        lines.append(f"\n... ({total - effective_limit} more files, {total} total)")

    return truncate("\n".join(lines))
