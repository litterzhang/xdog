"""Private runtime control uses the same current-context capability as delivery."""
from __future__ import annotations

import asyncio

from xdog.eyes.transport.protocol import (
    MAX_FRAME,
    HostContext,
    JsonValue,
    ProtocolError,
    decode_frame,
    encode_frame,
    validate_response,
)


async def request(context: HostContext, action: str, body: dict[str, JsonValue], *,
                  timeout: float = 5.0) -> dict[str, JsonValue]:
    frame = encode_frame({"version": 1, "action": action, "body": body,
                          "owner": context.owner.as_json(), "token": context.token})
    writer: asyncio.StreamWriter | None = None
    try:
        async with asyncio.timeout(125 if action == "resume" else timeout):
            reader, writer = await asyncio.open_unix_connection(context.directory / "eyes.sock", limit=MAX_FRAME)
            writer.write(frame)
            await writer.drain()
            try:
                response = await reader.readline()
            except ValueError as exc:
                raise ProtocolError("Runtime response exceeds frame capacity") from exc
            if not response.endswith(b"\n"):
                raise ConnectionError("Runtime ended before acknowledging the command")
            return validate_response(decode_frame(response))
    finally:
        if writer is not None:
            writer.close()
            try:
                async with asyncio.timeout(timeout):
                    await writer.wait_closed()
            except (OSError, TimeoutError):
                pass
