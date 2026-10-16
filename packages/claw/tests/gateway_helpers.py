"""Bounded offline Unix-socket test helpers; no model/service startup."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager


@asynccontextmanager
async def peer(config):
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(value):
        writer.write((json.dumps(value) + '\n').encode())
        await writer.drain()
    async def receive(kind):
        async with asyncio.timeout(10):
            while True:
                line = await reader.readline()
                assert line, 'Connection ended without an owned result'
                value = json.loads(line)
                if value['type'] == kind:
                    return value
    try:
        yield send, receive, writer
    finally:
        writer.close()
        await writer.wait_closed()


async def handshake(send, receive, **capabilities):
    await send({'type': 'ping', 'group_id': 'main', 'extension_session': 1, **capabilities})
    return await receive('pong')
