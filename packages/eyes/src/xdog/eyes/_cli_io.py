"""Bounded CLI JSON IO. Closing a pipe never changes observation authority."""
from __future__ import annotations

import asyncio
import errno
import io
import json
import os
import select
import stat
import sys
from pathlib import Path
from typing import Any, TextIO

from xdog.eyes.loader import _unique_pairs
from xdog.eyes.local_control import MAX_FRAME, JsonValue, decode_frame, encode_frame

MAX_INPUT_BYTES = MAX_FRAME - 4096


class OutputClosed(Exception):
    """The caller detached its output pipe, not its daemon registration."""


def read_stdin_json() -> dict[str, JsonValue]:
    stream = getattr(sys.stdin, 'buffer', sys.stdin)
    value = stream.read(MAX_INPUT_BYTES + 1)
    encoded = value.encode('utf-8') if isinstance(value, str) else value
    if len(encoded) > MAX_INPUT_BYTES:
        raise ValueError(f'Eyes stdin JSON exceeds {MAX_INPUT_BYTES} bytes')
    return decode_frame(encoded.strip() + b'\n')


def argument_json(value: str) -> dict[str, JsonValue]:
    encoded = value.encode('utf-8')
    if len(encoded) > MAX_INPUT_BYTES:
        raise ValueError('Eyes JSON argument exceeds capacity')
    return decode_frame(encoded.strip() + b'\n')


class JsonOutput:
    """At most one bounded frame buffered, with real pipe backpressure."""
    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.fd: int | None = None
        self.blocking = True
        self.poll: select.poll | None = None
        try:
            fd = self.stream.fileno()
            mode = os.fstat(fd).st_mode
            if stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
                self.fd, self.blocking = fd, os.get_blocking(fd)
                os.set_blocking(fd, False)
                self.poll = select.poll()
                self.poll.register(fd, select.POLLERR | select.POLLHUP | select.POLLNVAL)
        except (OSError, io.UnsupportedOperation):
            pass  # pytest/StringIO and regular text outputs need no pipe watcher.

    async def write(self, value: object) -> None:
        frame = encode_frame(value)
        try:
            if self.fd is None:
                self.stream.write(frame.decode('ascii'))
                self.stream.flush()
                return
            remaining = memoryview(frame)
            while remaining:
                try:
                    written = os.write(self.fd, remaining)
                    if not written:
                        raise OutputClosed
                    remaining = remaining[written:]
                except BlockingIOError:
                    ready = asyncio.get_running_loop().create_future()
                    loop = asyncio.get_running_loop()

                    def writable() -> None:
                        if not ready.done():
                            ready.set_result(None)

                    loop.add_writer(self.fd, writable)
                    try:
                        await ready
                    finally:
                        loop.remove_writer(self.fd)
        except BrokenPipeError as exc:
            raise OutputClosed from exc
        except OSError as exc:
            if exc.errno in {errno.EPIPE, errno.EBADF, errno.ECONNRESET}:
                raise OutputClosed from exc
            raise

    async def wait_closed(self) -> None:
        while True:
            if self.poll is not None and self.poll.poll(0):
                return
            await asyncio.sleep(.1)

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.set_blocking(self.fd, self.blocking)
            except OSError:
                pass


def _read_definition(value: str) -> tuple[dict[str, Any], Path]:
    """Finite file IO only. Destinations and observation roots belong to the JSON."""
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('Eyes definition_path must be absolute')
    if '..' in path.parts or path.resolve() != path:
        raise ValueError('Eyes definition must be a non-symlink regular JSON file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Eyes definition must be a regular file')
        content = stream.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024:
        raise ValueError('Eyes definition exceeds one MiB')
    data = json.loads(content, object_pairs_hook=_unique_pairs)
    if not isinstance(data, dict):
        raise ValueError('Eyes definition must be a JSON object')
    return data, path.parent
