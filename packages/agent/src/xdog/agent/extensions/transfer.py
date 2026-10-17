"""Owned checksummed context transfer, outside bounded individual RPC frames."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, BinaryIO

from xdog.agent.async_work import wait_owned

BLOCK = 64 * 1024
MAX_CONTEXT_BYTES = 128 * 1024 * 1024


def spool(value: Any) -> tuple[BinaryIO, int, str]:
    file = tempfile.TemporaryFile()
    try:
        encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False)
        digest = hashlib.sha256()
        size = 0
        for text in encoder.iterencode(value):
            data = text.encode("utf-8")
            size += len(data)
            if size > MAX_CONTEXT_BYTES:
                raise ValueError("Extension context exceeds the explicit transfer limit")
            file.write(data)
            digest.update(data)
        file.seek(0)
        return file, size, digest.hexdigest()
    except BaseException:
        file.close()
        raise


@asynccontextmanager
async def owned_spool(value: Any) -> AsyncIterator[tuple[BinaryIO, int, str]]:
    """Own resource creation as well as delivery, including late canceled results.

    A generic canceled thread helper cannot return its late tempfile to close it.
    Keep the task receipt here, join through repeated cancellation, then reclaim
    the actual result before allowing the transfer/provider owner to settle.
    """
    preparing = asyncio.create_task(asyncio.to_thread(spool, value))
    result: tuple[BinaryIO, int, str] | None = None
    try:
        result = await asyncio.shield(preparing)
        yield result
    finally:
        if result is None:
            try:
                await wait_owned(preparing)
            except (Exception, asyncio.CancelledError):
                pass  # The original entry failure/cancellation still owns delivery.
            if not preparing.cancelled():
                try:
                    result = preparing.result()  # Retrieve even when the join deferred a second cancel.
                except Exception:
                    pass  # Failed construction closes its own partially created file.
        if result is not None and not result[0].closed:
            result[0].close()


class ContextTransfer:
    def __init__(self) -> None:
        self.file: BinaryIO | None = None
        self.id = ""
        self.size = self.received = self.index = 0
        self.checksum = ""
        self.digest = hashlib.sha256()
        self.output: BinaryIO | None = None
        self.output_size = 0

    def close(self) -> None:
        for file in (self.file, self.output):
            if file is not None:
                file.close()
        self.file = self.output = None
        self.id = ""

    def begin(self, params: dict[str, Any]) -> None:
        self.close()
        identifier, size, checksum = params.get("id"), params.get("bytes"), params.get("sha256")
        if (not isinstance(identifier, str) or not 0 < len(identifier) <= 128
                or type(size) is not int or not 0 <= size <= MAX_CONTEXT_BYTES
                or not isinstance(checksum, str) or len(checksum) != 64
                or any(char not in "0123456789abcdef" for char in checksum)):
            raise ValueError("Invalid extension context manifest")
        self.file = tempfile.TemporaryFile()
        self.id, self.size, self.checksum = identifier, size, checksum
        self.received = self.index = 0
        self.digest = hashlib.sha256()

    def chunk(self, params: dict[str, Any]) -> None:
        if self.file is None or params.get("id") != self.id or params.get("index") != self.index:
            raise ValueError("Extension context transfer owner/order mismatch")
        data = params.get("data")
        if not isinstance(data, str) or len(data) > 87384:
            raise ValueError("Invalid extension context block")
        raw = base64.b64decode(data, validate=True)
        if len(raw) > BLOCK or self.received + len(raw) > self.size:
            raise ValueError("Extension context exceeds its manifest")
        self.file.write(raw)
        self.digest.update(raw)
        self.received += len(raw)
        self.index += 1

    def finish(self, params: dict[str, Any]) -> Any:
        if (self.file is None or params.get("id") != self.id or params.get("chunks") != self.index
                or self.received != self.size or self.digest.hexdigest() != self.checksum):
            raise ValueError("Incomplete extension context/checksum mismatch")
        self.file.seek(0)
        value = json.load(self.file)
        self.file.close()
        self.file = None
        return value

    def result(self, value: Any) -> dict[str, Any]:
        self.output, self.output_size, checksum = spool(value)
        return {"id": self.id, "bytes": self.output_size, "sha256": checksum}

    def read(self, params: dict[str, Any]) -> dict[str, Any]:
        index = params.get("index")
        if (self.output is None or params.get("id") != self.id or type(index) is not int or index < 0
                or index * BLOCK > self.output_size):
            raise ValueError("Invalid extension context result request")
        self.output.seek(index * BLOCK)
        block = self.output.read(BLOCK)
        return {"id": self.id, "index": index, "data": base64.b64encode(block).decode("ascii")}
