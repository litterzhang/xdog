"""Shared Unix control for daemon lifecycle, scoped registrations and data-only event reads."""

from __future__ import annotations

import asyncio

from xdog.eyes.local_control import (
    MAX_FRAME,
    ControlError,
    JsonValue,
    RuntimeLocation,
    decode_frame,
    encode_frame,
    read_location,
    validate_socket,
)


async def request(location: RuntimeLocation, action: str, body: dict[str, JsonValue], *,
                  timeout: float = 5.0) -> dict[str, JsonValue]:
    if not location.generation and action != 'hello':
        location = await asyncio.to_thread(read_location, location)
    await asyncio.to_thread(validate_socket, location)
    writer = None
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_unix_connection(location.socket, limit=MAX_FRAME)
            writer.write(encode_frame(
                {'version': 1, 'generation': location.generation, 'action': action, 'body': body}))
            await writer.drain()
            frame = await reader.readline()
            if not frame:
                raise ConnectionError('Eyes daemon closed the control connection before replying')
            response = decode_frame(frame)
            if (set(response) != {'version', 'generation', 'cwd', 'status', 'body'}
                    or type(response['version']) is not int or response['version'] != 1
                    or response['cwd'] != str(location.cwd)
                    or not isinstance(response['generation'], str)
                    or not isinstance(response['status'], str)
                    or response['status'] not in {'ok', 'accepted', 'duplicate', 'rejected', 'stale-generation'}
                    or not isinstance(response['body'], dict)):
                raise ControlError('Invalid Eyes control response')
            if (location.generation and response['generation'] != location.generation
                    and response['status'] != 'stale-generation'):
                raise ControlError('Eyes runtime generation changed')
            return response
    finally:
        if writer is not None:
            writer.close()
            try:
                async with asyncio.timeout(2):
                    await writer.wait_closed()
            except (OSError, TimeoutError):
                pass
