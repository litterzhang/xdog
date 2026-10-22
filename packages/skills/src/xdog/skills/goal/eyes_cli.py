"""Goal-owned CLI process IO, not an Eyes SDK or daemon protocol client.

Every operation executes the installed CLI with a fixed argv, never a shell.
Only JSON crosses this boundary. The event process owns no observer lifetime;
closing its stdout subscription cannot stop the shared daemon or accepted work.
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
from collections.abc import AsyncGenerator
from typing import Any

from xdog.agent.async_work import wait_owned

_MAX_OUTPUT = 1100_000
_MAX_ERROR = 16_384


class EyesCLIError(ValueError):
    """The fixed Eyes command failed, not permission to invoke an alternative."""


def _command(owner: str, arguments: tuple[str, ...]) -> tuple[str, ...]:
    return (sys.executable, '-m', 'xdog.eyes', '--owner', owner, *arguments)


def _object(payload: bytes) -> dict[str, Any]:
    def reject(value: str) -> None:
        raise ValueError('Nonfinite JSON value: ' + value)
    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('Nonfinite JSON number')
        return number
    try:
        value = json.loads(payload, parse_constant=reject, parse_float=finite_float)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise EyesCLIError('Invalid Eyes CLI JSON') from exc
    if not isinstance(value, dict):
        raise EyesCLIError('Expected an Eyes CLI JSON object')
    return value


async def _read(reader: asyncio.StreamReader, maximum: int) -> bytes:
    value = bytearray()
    while chunk := await reader.read(min(65_536, maximum + 1 - len(value))):
        value.extend(chunk)
        if len(value) > maximum:
            raise EyesCLIError('Eyes CLI output exceeds capacity')
    return bytes(value)


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            process.terminate()  # Only the CLI process we created, never its daemon.
        except ProcessLookupError:
            pass
        try:
            async with asyncio.timeout(3):
                await process.wait()
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
    await process.wait()


async def _spawn(owner: str, arguments: tuple[str, ...], *, input_pipe: bool = False
                 ) -> asyncio.subprocess.Process:
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(*_command(owner, arguments),
        stdin=asyncio.subprocess.PIPE if input_pipe else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=_MAX_OUTPUT))
    try:
        return await asyncio.shield(spawning)
    except asyncio.CancelledError:
        while not spawning.done():
            try:
                await asyncio.shield(spawning)
            except asyncio.CancelledError:
                pass
        await wait_owned(asyncio.create_task(_stop(spawning.result())))
        raise


async def _finish(process: asyncio.subprocess.Process, tasks: tuple[asyncio.Task[Any], ...]) -> None:
    try:
        await _stop(process)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def invoke(owner: str, *arguments: str,
                 payload: dict[str, Any] | None = None, timeout: float = 145) -> dict[str, Any]:
    encoded = None if payload is None else json.dumps(payload, allow_nan=False).encode()
    if encoded is not None and len(encoded) > _MAX_OUTPUT:
        raise EyesCLIError('Eyes CLI input exceeds capacity')
    process = await _spawn(owner, arguments, input_pipe=encoded is not None)
    assert process.stdout is not None and process.stderr is not None
    stdout = asyncio.create_task(_read(process.stdout, _MAX_OUTPUT))
    stderr = asyncio.create_task(_read(process.stderr, _MAX_ERROR))
    waiting = asyncio.create_task(process.wait())
    try:
        async with asyncio.timeout(timeout):
            if encoded is not None:
                assert process.stdin is not None
                process.stdin.write(encoded)
                await process.stdin.drain()
                process.stdin.close()
            output, error, code = await asyncio.gather(stdout, stderr, waiting)
        if code:
            raise EyesCLIError(error.decode('utf-8', errors='replace').strip() or f'Eyes CLI exited {code}')
        return _object(output)
    finally:
        await wait_owned(asyncio.create_task(_finish(process, (stdout, stderr, waiting))))


async def events(owner: str) -> AsyncGenerator[dict[str, Any], None]:
    process = await _spawn(owner, ('events', '--follow'))
    assert process.stdout is not None and process.stderr is not None
    errors = asyncio.create_task(_read(process.stderr, _MAX_ERROR))
    waiting = asyncio.create_task(process.wait())
    reading: asyncio.Task[bytes] | None = None
    try:
        while True:
            reading = asyncio.create_task(process.stdout.readline())
            if not errors.done():
                await asyncio.wait((reading, errors), return_when=asyncio.FIRST_COMPLETED)
            if errors.done() and errors.exception() is not None:
                await errors  # Oversized stderr must not strand an idle stdout reader.
            line = await reading
            if not line:
                break
            if len(line) > _MAX_OUTPUT:
                raise EyesCLIError('Eyes CLI event exceeds capacity')
            yield _object(line)
        # EOF ends the subscription even if a broken CLI keeps running after
        # closing stdout. Allow only bounded diagnostic settling, then fail and
        # close our original process in finally.
        await asyncio.wait((waiting, errors), timeout=.25)
        error = errors.result() if errors.done() else b''
        raise EyesCLIError(error.decode('utf-8', errors='replace').strip() or
                           f'Eyes CLI event stream ended (exit {process.returncode})')
    finally:
        tasks: tuple[asyncio.Task[Any], ...] = ((errors, waiting) if reading is None
                                               else (errors, waiting, reading))
        await wait_owned(asyncio.create_task(_finish(process, tasks)))
