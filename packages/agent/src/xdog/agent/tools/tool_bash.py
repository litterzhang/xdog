from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from xdog.agent.core import AgentTool, AgentToolResult
from xdog.agent.tools._utils import (
    _BASH_TEMP_THRESHOLD,
    _DEFAULT_BASH_TIMEOUT_MS,
    _MAX_BASH_TIMEOUT_MS,
    kill_process_tree,
    truncate,
    try_update_cwd,
)
from xdog.ai.types import TextContent


async def _noop() -> None:
    """Stand-in when a stream is absent.

    `create_subprocess_exec` types stdout/stderr as optional, and they
    really are None when the pipe was not requested — reading one
    unconditionally would raise rather than simply produce no output.
    """
    return None


async def _terminate_process_group(
    proc: asyncio.subprocess.Process,
    pgid: int,
    process_task: asyncio.Task[int],
    io_task: asyncio.Future[Any],
) -> None:
    """Terminate a process group, escalating after a short grace period."""
    kill_process_tree(proc, pgid=pgid)
    try:
        await asyncio.wait_for(
            asyncio.gather(process_task, io_task, return_exceptions=True),
            timeout=0.5,
        )
    except TimeoutError:
        kill_process_tree(proc, pgid=pgid, force=True)
        await asyncio.gather(process_task, io_task, return_exceptions=True)


def create_bash_tool(*, initial_cwd: Path | None = None) -> AgentTool:
    """Create a tool that executes bash commands with stateful CWD tracking.

    The working directory persists between calls. If a command contains
    ``cd``, the tool tracks the directory change for subsequent calls.

    Parameters
    ----------
    initial_cwd:
        Starting working directory. Defaults to ``Path.cwd()``.
    """
    state = {"cwd": (initial_cwd or Path.cwd()).resolve()}

    async def execute(
        tool_call_id: str,
        args: dict[str, Any],
        cancel: Any = None,
        on_update: Any = None,
        **kwargs: Any,
    ) -> AgentToolResult:
        command = args.get("command", "")
        if not command:
            return AgentToolResult(
                content=(TextContent(text="Error: no command provided"),),
            )

        timeout_ms = args.get("timeout_ms")
        if timeout_ms is not None:
            timeout_ms = max(1000, min(int(timeout_ms), _MAX_BASH_TIMEOUT_MS))
        else:
            timeout_ms = _DEFAULT_BASH_TIMEOUT_MS

        timeout_s = timeout_ms / 1000.0

        try:
            extra = kwargs.get("subprocess_env")
            if extra is not None and (not isinstance(extra, dict)
                    or any(not isinstance(key, str) or not isinstance(value, str) for key, value in extra.items())):
                raise ValueError("Caller subprocess environment must map strings to strings")
            environment = {**os.environ, **extra} if extra is not None else None
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(state["cwd"]),
                env=environment,
                start_new_session=True,
            )

            # start_new_session makes the shell the session/group leader. It
            # may already be reaped by asyncio for a very short command, so a
            # subsequent getpgid lookup can lose otherwise successful output.
            process_group = proc.pid
            stdout_lines: list[str] = []
            stderr_lines: list[str] = []
            update_lock = asyncio.Lock()

            async def _read_stream(
                stream: asyncio.StreamReader,
                lines_out: list[str],
            ) -> None:
                """Read stream line-by-line, calling on_update with progress."""
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    lines_out.append(line.decode("utf-8", errors="replace"))
                    if on_update is not None:
                        async with update_lock:
                            combined = "".join(stdout_lines + stderr_lines)
                            await on_update(AgentToolResult(
                                content=(TextContent(text=combined),),
                            ))

            io_task = asyncio.ensure_future(asyncio.gather(
                _read_stream(proc.stdout, stdout_lines) if proc.stdout else _noop(),
                _read_stream(proc.stderr, stderr_lines) if proc.stderr else _noop(),
            ))
            cancel_task: asyncio.Task[bool] | None = None
            if cancel is not None and hasattr(cancel, "wait"):
                cancel_task = asyncio.create_task(cancel.wait())

            process_task = asyncio.create_task(proc.wait())
            waiters: set[asyncio.Future[Any]] = {process_task, io_task}
            if cancel_task is not None:
                waiters.add(cancel_task)

            timed_out = False
            try:
                deadline = asyncio.get_running_loop().time() + timeout_s
                while not (process_task.done() and io_task.done()):
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        timed_out = True
                        break
                    done, _ = await asyncio.wait(
                        waiters,
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if cancel_task is not None and cancel_task in done:
                        await _terminate_process_group(
                            proc,
                            process_group,
                            process_task,
                            io_task,
                        )
                        raise asyncio.CancelledError
                    waiters = {
                        waiter for waiter in waiters
                        if not waiter.done() or waiter is cancel_task
                    }

                if timed_out:
                    await _terminate_process_group(
                        proc,
                        process_group,
                        process_task,
                        io_task,
                    )
                    return AgentToolResult(
                        content=(TextContent(
                            text=f"Command timed out after {timeout_ms}ms.",
                        ),),
                    )
                await asyncio.gather(process_task, io_task)
            finally:
                if cancel_task is not None and not cancel_task.done():
                    cancel_task.cancel()
                if not process_task.done():
                    process_task.cancel()

            stdout = "".join(stdout_lines)
            stderr = "".join(stderr_lines)
            exit_code = proc.returncode or 0

            # Track CWD changes
            state["cwd"] = try_update_cwd(command, state["cwd"])

            parts: list[str] = []
            if stdout:
                parts.append(stdout.rstrip())
            if stderr:
                parts.append(f"[stderr]\n{stderr.rstrip()}")
            if exit_code != 0:
                parts.append(f"[exit code: {exit_code}]")

            output = "\n".join(parts) if parts else "(no output)"

            # Write large output to temp file
            if len(output) > _BASH_TEMP_THRESHOLD:
                temp_path = Path(tempfile.gettempdir()) / f"bash_output_{uuid.uuid4().hex[:8]}.txt"
                temp_path.write_text(output, encoding="utf-8")
                truncated = truncate(output)
                truncated += f"\n\n[Full output written to {temp_path}]"
                return AgentToolResult(
                    content=(TextContent(text=truncated),),
                    details={"full_output_path": str(temp_path)},
                )

            return AgentToolResult(content=(TextContent(text=output),))

        except Exception as exc:
            return AgentToolResult(
                content=(TextContent(text=f"Error: {exc}"),),
            )

    return AgentTool(
        name="bash",
        description=(
            "Execute a bash command in the shell. The working directory persists "
            "between calls. Commands time out after 2 minutes by default (max 10 min). "
            "Use for git, npm, docker, builds, and other terminal operations."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute.",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": (
                        "Optional timeout in milliseconds (max 600000). "
                        "Defaults to 120000 (2 minutes)."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what the command does.",
                },
            },
            "required": ["command"],
        },
        execute=execute,
    )
