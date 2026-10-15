"""UID-local session ownership and discovery, not a hostile-process sandbox."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("Invalid session ID")
    return value


def private_directory(path: Path) -> Path:
    path = path.absolute()
    if path.resolve() != path:
        raise ValueError("Private directory must not contain symbolic links")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Runtime directory must be private (0700) and owned by current user")
    return path


def runtime_directory(sessions: Path) -> Path:
    # Short paths avoid Unix sun_path limits even for deeply nested project roots.
    root = private_directory(Path(tempfile.gettempdir()) / f"xdog-coding-{os.getuid()}")
    key = hashlib.sha256(str(sessions.resolve()).encode()).hexdigest()[:16]
    return private_directory(root / key)


def private_open(path: Path, flags: int) -> int:
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        os.close(fd)
        raise ValueError("Runtime files must be private regular files")
    return fd


class SessionLease:
    def __init__(self, directory: Path, session_id: str, fd: int) -> None:
        self.directory, self.session_id, self._fd = directory, session_id, fd

    @classmethod
    def acquire(cls, directory: Path, session_id: str) -> SessionLease:
        directory = private_directory(directory)
        session_id = identifier(session_id)
        fd = private_open(directory / (session_id + ".lock"), os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise RuntimeError("Session is already owned by another writer") from exc
        return cls(directory, session_id, fd)

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
        # Never unlink the lock inode: a waiter might otherwise lock a different file.


@dataclass(frozen=True)
class OwnerRecord:
    session_id: str
    cwd: Path
    address: Path
    generation: str


class OwnerRegistry:
    def __init__(self, directory: Path) -> None:
        self.directory = private_directory(directory)

    def _validate(self, record: OwnerRecord) -> None:
        identifier(record.session_id)
        if (
            not record.address.is_absolute()
            or record.address.parent != self.directory
            or record.address.is_symlink()
            or record.address.resolve().parent != self.directory
        ):
            raise ValueError("Owner address must be inside the private runtime directory")
        if not record.cwd.is_absolute() or not record.generation:
            raise ValueError("Invalid owner workspace/generation")

    def socket_path(self, session_id: str) -> Path:
        key = hashlib.sha256(identifier(session_id).encode()).hexdigest()[:24]
        return self.directory / ("s-" + key + ".sock")

    def publish(self, record: OwnerRecord) -> None:
        self._validate(record)
        path = self.directory / (record.session_id + ".json")
        if path.is_symlink():
            raise ValueError("Owner record must not be a symbolic link")
        fd, name = tempfile.mkstemp(prefix=".owner-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(asdict(record), stream, default=str)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def lookup(self, session_id: str) -> OwnerRecord:
        path = self.directory / (identifier(session_id) + ".json")
        try:
            fd = private_open(path, os.O_RDONLY)
        except FileNotFoundError as exc:
            raise RuntimeError("Session owner is inactive or unknown") from exc
        try:
            if os.fstat(fd).st_size > 16 * 1024:
                raise ValueError("Owner record exceeds capacity")
            with os.fdopen(fd, "r", closefd=False) as stream:
                value = json.load(stream)
        finally:
            os.close(fd)
        if not isinstance(value, dict) or set(value) != {"session_id", "cwd", "address", "generation"}:
            raise ValueError("Invalid owner record")
        if any(not isinstance(item, str) for item in value.values()) or value["session_id"] != session_id:
            raise ValueError("Invalid owner identity")
        result = OwnerRecord(value["session_id"], Path(value["cwd"]), Path(value["address"]), value["generation"])
        self._validate(result)
        return result

    def remove(self, record: OwnerRecord) -> None:
        try:
            current = self.lookup(record.session_id)
        except RuntimeError:
            return
        if current == record:
            (self.directory / (record.session_id + ".json")).unlink()
