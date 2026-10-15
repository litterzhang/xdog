"""Harness-neutral connection definitions; endpoint checks never discover a harness."""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

MAX_UNIX_PATH_BYTES = 103 if sys.platform == 'darwin' else 107


@dataclass(frozen=True)
class UnixConnection:
    path: Path
    transport: Literal['unix'] = 'unix'

    def __post_init__(self) -> None:
        if self.transport != 'unix' or not isinstance(self.path, Path) or not self.path.is_absolute():
            raise ValueError('ACP Unix endpoint must be an absolute path')
        encoded = str(self.path).encode('utf-8')
        if b'\x00' in encoded or len(encoded) > MAX_UNIX_PATH_BYTES:
            raise ValueError('ACP Unix endpoint path is invalid or exceeds local socket capacity')


@dataclass(frozen=True)
class StdioConnection:
    command: tuple[str, ...]
    transport: Literal['stdio'] = 'stdio'

    def __post_init__(self) -> None:
        if self.transport != 'stdio' or not isinstance(self.command, tuple) or not self.command:
            raise ValueError('ACP stdio requires nonempty argv')
        for arg in self.command:
            if not isinstance(arg, str) or not arg.strip() or '\x00' in arg or len(arg.encode('utf-8')) > 65536:
                raise ValueError('ACP argv requires nonempty UTF-8 text without NUL')


ConnectionDef: TypeAlias = UnixConnection | StdioConnection


def validate_endpoint(connection: UnixConnection) -> None:
    """Same-UID local admission, not a sandbox against another same-UID process."""
    path = connection.path
    if path.resolve() != path:
        raise ValueError('ACP endpoint path must not contain symbolic links or navigation components')
    directory, socket = path.parent.lstat(), path.lstat()
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) != 0o700):
        raise ValueError('ACP endpoint directory must be private (0700) and owned by current user')
    if (not stat.S_ISSOCK(socket.st_mode) or socket.st_uid != os.getuid()
            or stat.S_IMODE(socket.st_mode) != 0o600):
        raise ValueError('ACP endpoint must be a private (0600) Unix socket owned by current user')
