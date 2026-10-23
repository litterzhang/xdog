"""Serialize explicit runtime startup; a timeout or PID alone never proves death."""

from __future__ import annotations

import asyncio
import fcntl
import os
import subprocess
import sys
import time
from dataclasses import replace

from xdog.eyes.control import request
from xdog.eyes.local_control import RuntimeLocation, private_open, text, validate_location
from xdog.eyes.protocol import require_compatible

_children: dict[str, subprocess.Popen[bytes]] = {}


def _lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def reap_runtime(location: RuntimeLocation, *, timeout: float = 0.0) -> bool:
    key = str(location.directory)
    child = _children.get(key)
    if child is None:
        return False
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    _children.pop(key, None)
    return True


def ensure_runtime(location: RuntimeLocation, *, timeout: float = 8.0) -> RuntimeLocation:
    validate_location(location)
    deadline = time.monotonic() + timeout
    lock = private_open(location.directory / 'start.lock', os.O_CREAT | os.O_RDWR)
    child = None
    try:
        while not _lock(lock):
            if time.monotonic() >= deadline:
                raise TimeoutError('Another Eyes starter is still settling; no second process was started')
            time.sleep(0.02)
        reap_runtime(location)
        while time.monotonic() < deadline:
            refused = False
            try:
                response = asyncio.run(request(replace(location, generation=''), 'hello', {},
                                               timeout=min(0.25, max(0.01, deadline-time.monotonic()))))
                require_compatible(response)
                if response['status'] == 'ok':
                    result = replace(location, generation=text(response['generation']))
                    validate_location(result)
                    return result
                raise RuntimeError('Eyes runtime refused lifecycle control')
            except (FileNotFoundError, ConnectionRefusedError):
                refused = True
            except TimeoutError:
                pass  # Even with a free lease, a live/unresponsive listener cannot be replaced.
            if child is not None:
                if child.poll() is not None:
                    child.wait()
                    raise RuntimeError('Eyes runtime exited before becoming ready; inspect runtime.log')
                time.sleep(0.02)
                continue
            owner = private_open(location.directory / 'owner.lock', os.O_CREAT | os.O_RDWR)
            try:
                free = _lock(owner)
            finally:
                os.close(owner)
            if not refused or not free:
                time.sleep(0.02)
                continue
            log = private_open(location.directory / 'runtime.log', os.O_CREAT | os.O_WRONLY | os.O_APPEND)
            try:
                if os.fstat(log).st_size > 1024 * 1024:
                    os.ftruncate(log, 0)
                arguments = [sys.executable, '-m', 'xdog.eyes.runtime', '--directory', str(location.directory)]
                child = subprocess.Popen(arguments,
                    cwd=location.cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    close_fds=True, start_new_session=True)
                _children[str(location.directory)] = child
            finally:
                os.close(log)
        raise TimeoutError('Eyes owner/listener is not confirmed ready; no live process was replaced')
    finally:
        os.close(lock)  # Keep the lock inode; another waiter may already hold it open.
