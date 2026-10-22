"""UID-private finite JSON control and event reads; never an Agent execution endpoint."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TypeAlias, cast

JsonValue: TypeAlias = None | bool | int | float | str | list['JsonValue'] | dict[str, 'JsonValue']
MAX_FRAME = 1024 * 1024


class ControlError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeLocation:
    directory: Path
    socket: Path
    generation: str
    cwd: Path


def private_directory(path: Path) -> Path:
    path = path.absolute()
    if path.resolve() != path:
        raise ControlError('Eyes runtime paths must not contain symlinks or navigation')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ControlError('Eyes directory must be private (0700) and owned by current user')
    return path


def private_open(path: Path, flags: int) -> int:
    fd = os.open(path, (flags & ~os.O_TRUNC) | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        os.close(fd)
        raise ControlError('Eyes files must be private (0600) owned regular files')
    try:
        if flags & os.O_TRUNC:
            os.ftruncate(fd, 0)
        return fd
    except BaseException:
        os.close(fd)
        raise


def runtime_location(cwd: Path | None = None) -> RuntimeLocation:
    project = (cwd or Path.cwd()).resolve()
    if not project.is_dir():
        raise ControlError('Eyes project must be an existing directory')
    root = private_directory(Path(tempfile.gettempdir()) / f'xdog-eyes-{os.getuid()}')
    key = hashlib.sha256(str(project).encode()).hexdigest()[:16]
    directory = private_directory(root / key)
    return RuntimeLocation(directory, directory / 'eyes.sock', '', project)


def daemon_location(directory: Path | None = None) -> RuntimeLocation:
    """One explicitly opened daemon per UID, or an explicit private isolation root."""
    if directory is None and os.environ.get('XDOG_EYES_RUNTIME_DIR'):
        directory = Path(os.environ['XDOG_EYES_RUNTIME_DIR'])
    data = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local' / 'share')))
    root = private_directory(directory if directory is not None else data / 'xdog' / 'eyes')
    return RuntimeLocation(root, root / 'eyes.sock', '', root)


def validate_location(location: RuntimeLocation) -> None:
    expected = (daemon_location(location.directory) if location.cwd == location.directory
                else runtime_location(location.cwd))
    if location.directory != expected.directory or location.socket != expected.socket:
        raise ControlError('Eyes control address must match its project directory')
    if location.generation and not re.fullmatch('[0-9a-f]{32}', location.generation):
        raise ControlError('Invalid Eyes runtime generation')


def validate_socket(location: RuntimeLocation) -> None:
    validate_location(location)
    info = location.socket.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ControlError('Eyes endpoint must be a private owned Unix socket')


def finite(value: object, depth: int = 0) -> None:
    if depth > 32:
        raise ControlError('Eyes JSON nesting exceeds capacity')
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            finite(item, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            finite(item, depth + 1)
        return
    raise ControlError('Eyes control requires finite JSON')


def object_value(value: object, *, allowed: set[str] | None = None,
                 required: set[str] | None = None) -> dict[str, JsonValue]:
    finite(value)
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ControlError('Expected Eyes control object')
    if allowed is not None and set(value) - allowed or required is not None and required - set(value):
        raise ControlError('Invalid Eyes control fields')
    return cast(dict[str, JsonValue], value)


def text(value: object, *, empty: bool = False) -> str:
    if not isinstance(value, str) or not empty and not value.strip() or '\x00' in value:
        raise ControlError('Expected nonempty Eyes text')
    try:
        if len(value.encode()) > 4096:
            raise ControlError('Eyes text exceeds capacity')
    except UnicodeError as exc:
        raise ControlError('Eyes text must be UTF-8') from exc
    return value


def _unique(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ControlError('Duplicate Eyes control JSON key')
        result[key] = value
    return result


def encode_frame(value: object) -> bytes:
    finite(value)
    frame = json.dumps(value, ensure_ascii=True, allow_nan=False).encode() + b'\n'
    if len(frame) > MAX_FRAME:
        raise ControlError('Eyes control exceeds frame capacity')
    return frame


def decode_frame(frame: bytes) -> dict[str, JsonValue]:
    if not frame.endswith(b'\n') or len(frame) > MAX_FRAME:
        raise ControlError('Eyes control frame missing or oversized')
    try:
        return object_value(json.loads(frame, object_pairs_hook=_unique))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ControlError('Invalid Eyes control JSON: ' + str(exc)) from exc


def read_location(location: RuntimeLocation) -> RuntimeLocation:
    validate_location(location)
    fd = private_open(location.directory / 'owner.json', os.O_RDONLY)
    try:
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(4097)
        if len(data) > 4096:
            raise ControlError('Eyes owner record exceeds capacity')
        row = decode_frame(data)
        if (set(row) != {'version', 'generation', 'cwd', 'socket', 'pid'}
                or type(row['version']) is not int or row['version'] != 1
                or row['cwd'] != str(location.cwd) or row['socket'] != str(location.socket)
                or type(row['pid']) is not int or row['pid'] <= 0):
            raise ControlError('Invalid Eyes owner identity')
        result = replace(location, generation=text(row['generation']))
        validate_location(result)
        return result
    finally:
        os.close(fd)


def publish_location(location: RuntimeLocation) -> tuple[int, int]:
    path = location.directory / 'owner.json'
    if path.exists() or path.is_symlink():
        fd = private_open(path, os.O_RDONLY)
        os.close(fd)
    fd, name = tempfile.mkstemp(prefix='.owner-', dir=location.directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encode_frame({'version': 1, 'generation': location.generation,
                'cwd': str(location.cwd), 'socket': str(location.socket), 'pid': os.getpid()}))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        info = path.lstat()
        return info.st_dev, info.st_ino
    finally:
        if os.path.exists(name):
            os.unlink(name)


def unlink_owned(path: Path, identity: tuple[int, int] | None) -> None:
    if identity is not None and (path.exists() or path.is_symlink()):
        info = path.lstat()
        if info.st_uid == os.getuid() and (info.st_dev, info.st_ino) == identity:
            path.unlink()
