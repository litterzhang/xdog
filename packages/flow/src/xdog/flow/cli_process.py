"""Finite subprocess IO for the model tool; never an embedded workflow engine."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Any

from xdog.agent.async_work import wait_owned

_MAX_OUTPUT = 1024 * 1024  # Per stream. Overflow is explicit, never a success-shaped truncation.


def command(arguments: tuple[str, ...]) -> tuple[str, ...]:
    return (sys.executable, '-m', 'xdog.flow', *arguments)


async def _read(reader: asyncio.StreamReader) -> bytes:
    output = bytearray()
    while chunk := await reader.read(min(65_536, _MAX_OUTPUT + 1 - len(output))):
        output.extend(chunk)
        if len(output) > _MAX_OUTPUT:
            raise ValueError('Flow CLI output exceeds one MiB per stream; use CLI output files or smaller results. '
                             'The command was stopped; inspect effects before retrying.')
    return bytes(output)


def _signal(process: asyncio.subprocess.Process, value: signal.Signals) -> None:
    try:
        os.killpg(process.pid, value)
    except ProcessLookupError:
        pass


async def _stop(process: asyncio.subprocess.Process) -> None:
    # start_new_session pins a separate process group for this call. Never signal
    # the host, another invocation, or external service managers launched by CLI.
    _signal(process, signal.SIGTERM)
    try:
        async with asyncio.timeout(1):
            await process.wait()
    except TimeoutError:
        pass
    finally:
        # The leader may exit on TERM while a workflow child ignores it.
        _signal(process, signal.SIGKILL)
    await process.wait()


async def _spawn(arguments: tuple[str, ...], cwd: Path | None) -> asyncio.subprocess.Process:
    starting = asyncio.create_task(asyncio.create_subprocess_exec(*command(arguments), cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True))
    try:
        return await asyncio.shield(starting)
    except asyncio.CancelledError:
        # Cancellation can arrive after OS creation but before the handle is
        # published. Settle that exact spawn, then reclaim only its process group.
        while not starting.done():
            try:
                await asyncio.shield(starting)
            except asyncio.CancelledError:
                pass
        await wait_owned(asyncio.create_task(_stop(starting.result())))
        raise


async def _finish(process: asyncio.subprocess.Process, tasks: tuple[asyncio.Task[Any], ...]) -> None:
    try:
        await _stop(process)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run(arguments: tuple[str, ...], cwd: Path | None) -> dict[str, Any]:
    """Return actual CLI output/exit status, without retrying effects or parsing prose."""
    process = await _spawn(arguments, cwd)
    assert process.stdout is not None and process.stderr is not None
    stdout = asyncio.create_task(_read(process.stdout))
    stderr = asyncio.create_task(_read(process.stderr))
    waiting = asyncio.create_task(process.wait())
    try:
        output, error, code = await asyncio.gather(stdout, stderr, waiting)
        return {'exit_code': code, 'stdout': output.decode('utf-8', errors='replace'),
                'stderr': error.decode('utf-8', errors='replace')}
    finally:
        await wait_owned(asyncio.create_task(_finish(process, (stdout, stderr, waiting))))
