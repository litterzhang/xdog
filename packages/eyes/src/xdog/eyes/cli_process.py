"""Finite subprocess IO for the model tool; never an embedded observation engine."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Any

_MAX_OUTPUT = 1024 * 1024  # Per stream. Overflow is explicit, never a success-shaped truncation.


def command(arguments: tuple[str, ...]) -> tuple[str, ...]:
    return (sys.executable, '-m', 'xdog.eyes', *arguments)


async def _read(reader: asyncio.StreamReader) -> bytes:
    output = bytearray()
    while chunk := await reader.read(min(65_536, _MAX_OUTPUT + 1 - len(output))):
        output.extend(chunk)
        if len(output) > _MAX_OUTPUT:
            raise ValueError('Eyes CLI output exceeds one MiB per stream; use CLI output files or smaller results. '
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
        # The leader may exit on TERM while an observation child ignores it.
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
        await wait_owned(asyncio.create_task(_finish(starting.result(), ())))
        raise


async def _drain(reader: asyncio.StreamReader) -> None:
    # Discard after detachment; never retain unbounded pipe output during stop.
    while await reader.read(65_536):
        pass


async def _finish(process: asyncio.subprocess.Process, tasks: tuple[asyncio.Task[Any], ...]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    results: list[object] = list(await asyncio.gather(*tasks, return_exceptions=True))
    # A reader may have failed at capacity while its pipe is still full. Keep
    # draining without retaining bytes, otherwise Process.wait can hang on EOF
    # publication even after the child was killed.
    drains = tuple(asyncio.create_task(_drain(reader)) for reader in (process.stdout, process.stderr)
                   if reader is not None)
    try:
        await _stop(process)
    except BaseException as exc:
        results.append(exc)
    finally:
        for drain in drains:
            drain.cancel()
        results.extend(await asyncio.gather(*drains, return_exceptions=True))
    _raise_failures(results)


async def _run(arguments: tuple[str, ...], cwd: Path | None) -> dict[str, Any]:
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


async def wait_owned(task: asyncio.Task[Any], *, propagate_cancel: bool = False) -> Any:
    """Settle owned cleanup; real failure always takes priority over cancellation.

    Finally blocks already propagate their caller's cancellation. External close
    additionally re-raises cancellation, but only after its owners have settled.
    """
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled and propagate_cancel:
        raise asyncio.CancelledError
    return result


async def _join(tasks: tuple[asyncio.Task[Any], ...]) -> None:
    """Join every owner before reporting failures; cancellation is not a failure.

    Cleanup errors must survive a simultaneous caller cancellation. In particular,
    gather(return_exceptions=True) is only a collection step, never suppression.
    """
    results = await asyncio.gather(*tasks, return_exceptions=True)
    _raise_failures(results)


def _raise_failures(results: list[object]) -> None:
    failures = [result for result in results
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError)]
    if len(failures) == 1:
        raise failures[0]
    if failures:
        raise BaseExceptionGroup('Eyes CLI owned task failures', failures)


class CliProcess:
    """Own invocation waits, never shared daemon lifetime or accepted effects."""

    def __init__(self) -> None:
        self.closed = False
        self._calls: set[asyncio.Task[dict[str, Any]]] = set()

    async def run(self, arguments: tuple[str, ...], *, cancel: asyncio.Event | None = None) -> dict[str, Any]:
        if self.closed:
            raise RuntimeError('Eyes CLI binding is closed')
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        task = asyncio.create_task(self._invoke(arguments, cancel))
        self._calls.add(task)
        try:
            return await asyncio.shield(task)
        finally:
            task.cancel()
            # Joining the invocation also joins its pipe readers, spawn and event
            # watcher. Closing never leaves a watcher in the host loop.
            try:
                await wait_owned(asyncio.create_task(_join((task,))))
            finally:
                self._calls.discard(task)

    async def _invoke(self, arguments: tuple[str, ...], cancel: asyncio.Event | None) -> dict[str, Any]:
        task = asyncio.create_task(_run(arguments, None))
        aborted = asyncio.create_task(cancel.wait()) if cancel is not None else None
        try:
            if aborted is not None:
                done, _ = await asyncio.wait({task, aborted}, return_when=asyncio.FIRST_COMPLETED)
                if aborted in done:
                    raise asyncio.CancelledError
            return await asyncio.shield(task)
        finally:
            task.cancel()
            if aborted is not None:
                aborted.cancel()
            async def finish() -> None:
                await _join((task, aborted) if aborted is not None else (task,))
            await wait_owned(asyncio.create_task(finish()))

    async def aclose(self) -> None:
        self.closed = True
        calls = tuple(self._calls)
        for task in calls:
            task.cancel()
        try:
            await wait_owned(asyncio.create_task(_join(calls)), propagate_cancel=True)
        finally:
            self._calls.difference_update(calls)
