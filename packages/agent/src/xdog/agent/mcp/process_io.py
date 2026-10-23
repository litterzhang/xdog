"""Settle owned subprocess pipes before their event loop can be retired."""
from __future__ import annotations

import asyncio
from typing import cast

from xdog.agent.async_work import settle_cleanup


async def _discard(reader: asyncio.StreamReader | None) -> None:
    if reader is not None:
        while await reader.read(65_536):
            pass  # Discard bounded chunks; never retain arbitrary shutdown output.


async def close_process_pipes(process: asyncio.subprocess.Process) -> None:
    """Close all pipes and join their EOF callbacks on the original process loop.

    The caller must first stop its protocol readers and terminate its owned child.
    Process.wait() alone is insufficient: once returncode is known it can return
    while a backpressured pipe still owns a transport. asyncio Process exposes no
    public close operation; keep its transport access confined to this helper.
    This does not select another process, retry work or own a service lifetime.
    """
    transport = cast(asyncio.SubprocessTransport, getattr(process, '_transport'))
    transport.close()

    async def stdout() -> None:
        await _discard(process.stdout)

    async def stderr() -> None:
        await _discard(process.stderr)

    async def stdin() -> None:
        if process.stdin is not None:
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass  # The owned child was already terminated; its input cannot be written again.

    await settle_cleanup(stdout, stderr, stdin, process.wait, message='Subprocess pipe cleanup failed')
