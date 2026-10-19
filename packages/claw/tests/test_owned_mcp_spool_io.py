"""MCP previews share source IO ownership without executing tools or a model."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.claw.core.runtime import mcp_protocol

from packages.claw.tests.test_mcp_sender import document, request


@pytest.mark.parametrize('stage', ['preparation', 'read', 'close'])
async def test_repeated_mcp_close_cancel_settles_actual_preview_spool(monkeypatch, stage):
    entered, release, exited = (threading.Event() for _ in range(3))
    owner = threading.get_ident()
    files, closes = [], []
    original = mcp_protocol.spool_mcp_data

    def boundary():
        entered.set()
        if threading.get_ident() != owner:
            assert release.wait(5)
        exited.set()

    class ActualFile:
        def __init__(self, file):
            self.file = file

        def read(self, size=-1):
            value = self.file.read(size)
            if stage == 'read' and not entered.is_set():
                boundary()
            return value

        def close(self):
            self.file.close()
            closes.append(threading.get_ident())
            if stage == 'close':
                boundary()

    def prepare(value, **kwargs):
        file, size, checksum = original(value, **kwargs)
        actual = ActualFile(file)
        files.append(actual)
        if stage == 'preparation':
            boundary()
        return actual, size, checksum

    async def fetch(value):
        return document()  # External data boundary only; actual spool/IO is unmodified.

    async def write(frame):
        pass

    monkeypatch.setattr(mcp_protocol, 'spool_mcp_data', prepare)
    sender = mcp_protocol.McpSender(fetch, lambda _group: ('session', 1), write)
    closing = None
    try:
        sender.request(request())
        assert await asyncio.to_thread(entered.wait, 3)
        closing = asyncio.create_task(sender.close())
        await asyncio.sleep(0)
        for _ in range(3):
            closing.cancel()
            await asyncio.sleep(0)
        assert not closing.done() and not exited.is_set(), (
            'MCP released its actual spool owner before disk work settled')
        assert sender._active == 'one'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert files[0].file.closed and sender._active == ''
        assert len(closes) == 1 and closes[0] != owner
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await sender.close()
        await asyncio.to_thread(exited.wait, 3)
        if files and not files[0].file.closed:
            await asyncio.to_thread(files[0].file.close)
