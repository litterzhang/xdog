"""Explicitly managed ACP ingress; independent of TUI and AgentSession lifetimes."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from xdog.agent.acp.adapter import AcpAdapter
from xdog.agent.acp.transport import serve
from xdog.agent.async_work import wait_owned
from xdog.coding.acp.ingress_backend import IngressBackend
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.coding.core.sessions.ownership import OwnerRegistry, private_directory, private_open, runtime_directory


@dataclass(frozen=True)
class IngressConfig:
    sessions_dir: Path
    inbox_dir: Path
    address: Path

    def __post_init__(self) -> None:
        for path in (self.sessions_dir, self.inbox_dir, self.address):
            if not isinstance(path, Path) or not path.is_absolute() or path.resolve() != path:
                raise ValueError('ACP ingress paths must be absolute without symlinks/navigation')
        if '\x00' in str(self.address) or len(str(self.address).encode()) > 107:
            raise ValueError('ACP ingress socket path exceeds local capacity')


class IngressServer:
    def __init__(self, config: IngressConfig) -> None:
        self.config = config
        self.generation = uuid.uuid4().hex
        self.server: asyncio.AbstractServer | None = None
        self._lease = -1
        self._directory: Path | None = None
        self._identity: tuple[int, int] | None = None
        self._record_identity: tuple[int, int] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._closing: asyncio.Task[None] | None = None

    def _acquire(self) -> tuple[int, Path]:
        directory = runtime_directory(self.config.sessions_dir)
        fd = private_open(directory / '.acp-ingress.lock', os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise RuntimeError('ACP ingress is already running for this session store') from exc
        return fd, directory

    @classmethod
    async def start(cls, config: IngressConfig) -> IngressServer:
        self = cls(config)
        acquire = asyncio.create_task(asyncio.to_thread(self._acquire))
        try:
            cancelled = False
            while not acquire.done():
                try:
                    await asyncio.shield(acquire)
                except asyncio.CancelledError:
                    cancelled = True
            self._lease, self._directory = acquire.result()
            if cancelled:
                raise asyncio.CancelledError
            await asyncio.to_thread(private_directory, config.address.parent)
            await self._prepare_address()
            self.server = await asyncio.start_unix_server(self._connected, path=str(config.address),
                                                         limit=1024 * 1024, start_serving=False)
            info = config.address.lstat()
            self._identity = info.st_dev, info.st_ino
            os.chmod(config.address, 0o600)
            publish = asyncio.create_task(asyncio.to_thread(self._publish))
            cancelled = False
            while not publish.done():
                try:
                    await asyncio.shield(publish)
                except asyncio.CancelledError:
                    cancelled = True
            publish.result()
            if cancelled:
                raise asyncio.CancelledError
            await self.server.start_serving()
            return self
        except BaseException:
            await self.aclose()
            raise

    async def _prepare_address(self) -> None:
        path = self.config.address
        if not path.exists() and not path.is_symlink():
            return
        info = path.lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('Refusing insecure or non-socket ACP endpoint')
        try:
            async with asyncio.timeout(1):
                reader, writer = await asyncio.open_unix_connection(path)
        except ConnectionRefusedError:
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise RuntimeError('ACP socket identity changed during startup') from None
            path.unlink()
            return
        else:
            writer.close()
            await writer.wait_closed()
            raise RuntimeError('ACP address has a live listener; refusing replacement')

    def _publish(self) -> None:
        assert self._directory is not None
        path = self._directory / '.acp-ingress.json'
        if path.exists() or path.is_symlink():
            fd = private_open(path, os.O_RDONLY)
            os.close(fd)
        fd, name = tempfile.mkstemp(prefix='.ingress-', dir=self._directory)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'generation': self.generation, 'address': str(self.config.address),
                           'pid': os.getpid()}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            info = path.lstat()
            self._record_identity = info.st_dev, info.st_ino
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closing is not None:
            writer.close()
            return
        task = asyncio.create_task(self._accept(reader, writer))
        self._tasks.add(task)
        self._writers.add(writer)
        task.add_done_callback(self._tasks.discard)

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        assert self._directory is not None
        backend = IngressBackend(InboxStore(self.config.sessions_dir, self.config.inbox_dir),
                                 OwnerRegistry(self._directory))
        try:
            await serve(AcpAdapter(backend), reader, writer)
        except (ConnectionError, OSError, ValueError):
            pass  # Malformed/disconnected peer does not affect another owner/client.
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            self._writers.discard(writer)

    async def aclose(self) -> None:
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        await wait_owned(self._closing)

    async def _close(self) -> None:
        try:
            if self.server is not None:
                self.server.close()
            for writer in tuple(self._writers):
                writer.close()
            if self.server is not None:
                await self.server.wait_closed()
            tasks = tuple(self._tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._unlink_owned(self.config.address, self._identity)
            if self._directory is not None:
                self._unlink_owned(self._directory / '.acp-ingress.json', self._record_identity)
        finally:
            if self._lease >= 0:
                os.close(self._lease)
                self._lease = -1

    @staticmethod
    def _unlink_owned(path: Path, identity: tuple[int, int] | None) -> None:
        if identity is not None and (path.exists() or path.is_symlink()):
            info = path.lstat()
            if (info.st_dev, info.st_ino) == identity and info.st_uid == os.getuid():
                path.unlink()
