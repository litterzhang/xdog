"""Private daemon lifetime for tests, including child interpreters and hosts."""
from __future__ import annotations

import asyncio
import fcntl
import os
import shutil
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest


def _lease_free(directory: Path) -> bool:
    path = directory / 'owner.lock'
    if not path.exists():
        return True
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True
    finally:
        os.close(fd)


async def stop_test_daemon(directory: Path) -> None:
    """Use only the CLI to stop this test instance; never resolve a user default."""
    if not directory.exists():
        return
    async with asyncio.timeout(35):
        while (directory / 'eyes.sock').exists() or not _lease_free(directory):
            process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'xdog.eyes',
                '--directory', str(directory), 'daemon', 'stop',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            output, error = await process.communicate()
            assert process.returncode == 0, (output, error)
            await asyncio.sleep(.02)
    assert not (directory / 'eyes.sock').exists() and _lease_free(directory)


@asynccontextmanager
async def isolated_eyes_daemon(*, prefix: str = 'test-eyes-') -> AsyncIterator[Path]:
    # Short socket paths; both controls are inherited by child interpreters.
    root = Path(tempfile.mkdtemp(prefix=prefix, dir='/tmp'))
    directory = root / 'daemon'
    environment = pytest.MonkeyPatch()
    environment.setenv('XDG_STATE_HOME', str(root / 'state'))
    environment.setenv('XDOG_EYES_RUNTIME_DIR', str(directory))
    try:
        yield directory
    finally:
        try:
            await stop_test_daemon(directory)
            shutil.rmtree(root)
        finally:
            environment.undo()  # Failed cleanup retains only the private test state.
