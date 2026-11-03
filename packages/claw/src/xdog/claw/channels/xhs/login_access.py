"""Host-local exclusion for an explicit scan and browser-producing identity reads.

This is an advisory file lease, not persisted login state or a backend lifecycle.
Other clients of the same backend must also avoid creating browsers during a scan.
"""
from __future__ import annotations

import asyncio
import fcntl
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from xdog.agent.async_work import run_owned, wait_owned


class LoginBusy(ValueError):
    """The host already owns an interactive scan or an identity read."""


@contextmanager
def _lease(data_dir: Path, exclusive: bool) -> Iterator[None]:
    directory = data_dir / 'xhs'
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / 'login.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LoginBusy('XHS login or identity verification is already in progress; '
                            'finish it before retrying. No new QR was requested.') from exc
        yield
    finally:
        os.close(fd)


@asynccontextmanager
async def login_access(data_dir: Path, *, exclusive: bool = False) -> AsyncIterator[None]:
    lease = _lease(data_dir, exclusive)
    entering = asyncio.create_task(asyncio.to_thread(lease.__enter__))
    try:
        await wait_owned(entering)
        yield
    finally:
        if entering.done() and not entering.cancelled() and entering.exception() is None:
            await run_owned(lease.__exit__, None, None, None)
