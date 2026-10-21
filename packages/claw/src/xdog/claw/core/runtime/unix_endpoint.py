"""Owned Unix socket acquisition/cleanup before publication, not an execution host."""
from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import socket
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class BoundEndpoint:
    socket: socket.socket
    path: Path
    identity: tuple[int, int]
    lease: BinaryIO | None = None


def close(endpoint: BoundEndpoint) -> None:
    endpoint.socket.close()
    try:
        try:
            value = endpoint.path.lstat()
        except FileNotFoundError:
            return
        if (stat.S_ISSOCK(value.st_mode) and (value.st_dev, value.st_ino) == endpoint.identity):
            endpoint.path.unlink()
    finally:
        if endpoint.lease is not None:
            endpoint.lease.close()


def bind(path: Path) -> BoundEndpoint:
    return _bind_recoverable(path)


def _bind_recoverable(path: Path) -> BoundEndpoint:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = path.lstat()
    except FileNotFoundError:
        value = None
    if value is not None:
        if not stat.S_ISSOCK(value.st_mode) or value.st_uid != os.getuid():
            raise ValueError('Native socket destination is not an owned stale socket')
        # A configured collision must not detach another running receiver.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            outcome = probe.connect_ex(str(path))
        if outcome not in {errno.ECONNREFUSED, errno.ENOENT}:
            raise RuntimeError('Native socket is already live or unavailable')
        try:
            current = path.lstat()
        except FileNotFoundError:
            current = None
        if current is not None:
            if (current.st_dev, current.st_ino) != (value.st_dev, value.st_ino):
                raise ValueError('Native socket destination changed before stale cleanup')
            path.unlink()
    return _bind_new(path)


def bind_private(path: Path, *, recover_stale: bool = False) -> BoundEndpoint:
    """Keep explicit endpoints strict; an owned managed endpoint may recover stale sockets."""
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory = path.parent.lstat()
    if (path.parent.resolve() != path.parent or not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) != 0o700):
        raise ValueError("ACP endpoint directory must be owned and private (0700), without symlinks")
    if recover_stale:
        # Keep a lifetime lease: a competing gateway must not mistake the gap
        # between bind() and listen() for a crashed endpoint. Never unlink the
        # lease file; all starts must lock the same inode.
        fd = os.open(path.with_name(path.name + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            value = os.fstat(fd)
            if not stat.S_ISREG(value.st_mode) or value.st_uid != os.getuid() or value.st_nlink != 1:
                raise ValueError("Managed ACP lease must be an owned regular file")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Managed ACP endpoint is already owned by another gateway") from None
            return replace(_bind_recoverable(path), lease=os.fdopen(fd, "rb"))
        except BaseException:
            os.close(fd)
            raise
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("ACP endpoint already exists; stop its owner/remove a confirmed stale socket explicitly")
    return _bind_new(path)


def _bind_new(path: Path) -> BoundEndpoint:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    endpoint = None
    try:
        sock.bind(str(path))
        value = path.lstat()
        if not stat.S_ISSOCK(value.st_mode) or value.st_uid != os.getuid():
            raise ValueError("Bound socket path changed before acquisition; replacement left untouched")
        endpoint = BoundEndpoint(sock, path, (value.st_dev, value.st_ino))
        os.chmod(path, 0o600)  # No listen/accept before confidentiality is established.
        sock.setblocking(False)
        return endpoint
    except BaseException:
        if endpoint is not None:
            close(endpoint)
        else:
            sock.close()
        raise


async def listen(callback: Callable[[asyncio.StreamReader, asyncio.StreamWriter], None],
                 endpoint: BoundEndpoint, *, limit: int) -> asyncio.Server:
    """Adopt a prepared descriptor; only its inode owner may remove the path."""
    if sys.version_info >= (3, 13):
        return await asyncio.start_unix_server(callback, sock=endpoint.socket, limit=limit, cleanup_socket=False)
    return await asyncio.start_unix_server(callback, sock=endpoint.socket, limit=limit)
