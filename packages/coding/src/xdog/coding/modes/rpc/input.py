"""Bounded UTF-8 JSONL input; oversized records are drained, never parsed as fragments."""
from __future__ import annotations

import asyncio
import sys
from typing import Any

MAX_RPC_LINE_BYTES = 64 * 1024 * 1024  # Preserves four legacy 10 MiB inline references plus JSON overhead.


class RpcInput:
    def __init__(self, *, maximum: int = MAX_RPC_LINE_BYTES) -> None:
        if type(maximum) is not int or not 0 < maximum <= MAX_RPC_LINE_BYTES:
            raise ValueError('RPC frame limit must be a positive bounded integer')
        self.maximum = maximum
        self.reader: asyncio.StreamReader | None = None
        self.transport: Any = None

    async def open(self) -> None:
        source = getattr(sys.stdin, 'buffer', None)
        if source is None:
            return
        try:
            source.fileno()
            self.reader = asyncio.StreamReader(limit=self.maximum + 1)
            protocol = asyncio.StreamReaderProtocol(self.reader)
            self.transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, source)
        except (AttributeError, OSError, ValueError, NotImplementedError):
            self.reader = None  # In-memory/test/unsupported-platform readers retain sized reads below.

    def _fallback(self) -> tuple[str | None, str]:
        source = getattr(sys.stdin, 'buffer', sys.stdin)
        raw = source.readline(self.maximum + 1)
        if not raw:
            return None, ''
        size = len(raw) if isinstance(raw, bytes) else len(raw.encode('utf-8', errors='surrogatepass'))
        if size > self.maximum:
            while raw and not (raw.endswith(b'\n') if isinstance(raw, bytes) else raw.endswith('\n')):
                raw = source.readline(self.maximum + 1)
            return '', 'RPC record exceeds frame limit; discarded without executing fragments'
        try:
            return raw.decode('utf-8') if isinstance(raw, bytes) else raw, ''
        except UnicodeDecodeError:
            return '', 'RPC input must be UTF-8'

    async def read(self) -> tuple[str | None, str]:
        if self.reader is None:
            return await asyncio.to_thread(self._fallback)
        oversized = False
        while True:
            try:
                raw = await self.reader.readuntil(b'\n')
                break
            except asyncio.LimitOverrunError as exc:
                oversized = True
                await self.reader.readexactly(exc.consumed)
            except asyncio.IncompleteReadError as exc:
                raw = exc.partial
                if not raw and not oversized:
                    return None, ''
                break
        if oversized or len(raw) > self.maximum:
            return '', 'RPC record exceeds frame limit; discarded without executing fragments'
        try:
            return raw.decode('utf-8'), ''
        except UnicodeDecodeError:
            return '', 'RPC input must be UTF-8'

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
