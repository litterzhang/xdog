"""Owned Unix socket acquisition/cleanup before publication, not an execution host."""
from __future__ import annotations

import errno
import os
import socket
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BoundEndpoint:
    socket: socket.socket
    path: Path
    identity: tuple[int, int]


def close(endpoint: BoundEndpoint) -> None:
    endpoint.socket.close()
    try:
        value = endpoint.path.lstat()
    except FileNotFoundError:
        return
    if (stat.S_ISSOCK(value.st_mode) and (value.st_dev, value.st_ino) == endpoint.identity):
        endpoint.path.unlink()


def bind(path: Path) -> BoundEndpoint:
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
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    endpoint = None
    try:
        sock.bind(str(path))
        value = path.lstat()
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
