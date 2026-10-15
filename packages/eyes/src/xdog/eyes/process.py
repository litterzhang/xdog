"""Serialize starts and never infer process death from an observation timeout."""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

from xdog.eyes.control import request
from xdog.eyes.transport.protocol import HostContext, ProtocolError, load_context


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie is terminal even before its parent reaps it (Linux); elsewhere
    # conservatively rely on kill(pid, 0), never kill a possibly reused PID.
    path = Path(f"/proc/{pid}/stat")
    try:
        return path.read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def _private_open(path: Path, flags: int) -> int:
    fd = os.open(path, (flags & ~os.O_TRUNC) | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o777 != 0o600:
        os.close(fd)
        raise ProtocolError("Runtime files must be private and owned by the current user")
    if flags & os.O_TRUNC:
        try:
            os.ftruncate(fd, 0)
        except BaseException:
            os.close(fd)
            raise
    return fd


def ensure_runtime(context: HostContext) -> None:
    lock = _private_open(context.directory / "eyes-start.lock", os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = load_context(context.descriptor)
        if current.owner != context.owner or current.token != context.token:
            raise ProtocolError("Current session capability changed")
        record = context.directory / "eyes-runtime.json"
        deadline = time.monotonic() + 6
        child: subprocess.Popen[bytes] | None = None
        while time.monotonic() < deadline:
            try:
                response = asyncio.run(request(context, "hello", {}, timeout=0.5))
                if response["status"] == "ok":
                    return
                if response["status"] != "stale-owner":
                    raise ProtocolError("Runtime refused current-session control")
            except (OSError, TimeoutError):
                pass
            if child is not None:
                if child.poll() is not None:
                    raise RuntimeError("Observer runtime exited before becoming ready; inspect its private log")
                time.sleep(0.05)
                continue
            pid = 0
            if record.exists():
                fd = _private_open(record, os.O_RDONLY)
                try:
                    with os.fdopen(fd, "r", closefd=False) as stream:
                        value = json.load(stream)
                finally:
                    os.close(fd)
                if not isinstance(value, dict) or type(value.get("pid")) is not int or value["pid"] <= 0:
                    raise ProtocolError("Cannot verify existing runtime process identity")
                pid = value["pid"]
            if pid and alive(pid):
                time.sleep(0.05)
                continue
            socket = context.directory / "eyes.sock"
            if socket.exists():
                metadata = socket.lstat()
                if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
                    raise ProtocolError("Refusing to replace an unowned runtime socket")
                socket.unlink()
            log = _private_open(context.directory / "eyes-runtime.log", os.O_CREAT | os.O_WRONLY | os.O_APPEND)
            try:
                if os.fstat(log).st_size > 1024 * 1024:
                    os.ftruncate(log, 0)
                child = subprocess.Popen([sys.executable, "-m", "xdog.eyes.runtime", "--context",
                                          str(context.descriptor)], stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=log, close_fds=True, start_new_session=True)
            finally:
                os.close(log)
            fd = _private_open(record, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
            with os.fdopen(fd, "w") as stream:
                json.dump({"pid": child.pid, "owner": context.owner.as_json()}, stream)
                stream.flush()
                os.fsync(stream.fileno())
        raise TimeoutError("Existing observer process is alive but its control endpoint is not ready")
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
