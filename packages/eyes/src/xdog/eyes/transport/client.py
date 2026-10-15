"""Independent requests avoid interleaving parallel route deliveries."""
from __future__ import annotations

import asyncio

from xdog.eyes.transport.protocol import (
    MAX_FRAME,
    HostContext,
    JsonValue,
    ProtocolError,
    decode_frame,
    encode_frame,
    object_value,
    validate_request,
    validate_response,
)


class HostClient:
    def __init__(self, context: HostContext, *, timeout: float = 5.0) -> None:
        self.context = context
        self.timeout = timeout
        self._writers: set[asyncio.StreamWriter] = set()
        self._closed = False

    async def request(self, message: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if self._closed:
            raise ProtocolError("Host client is closed")
        object_value(message, allowed={"action", "body"}, required={"action", "body"})
        request: dict[str, JsonValue] = {
            "version": 1, "action": message["action"], "body": message["body"],
            "token": self.context.token, "owner": self.context.owner.as_json(),
        }
        frame = encode_frame(validate_request(request))
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(120 if message["action"] == "resume" else self.timeout):
                reader, writer = await asyncio.open_unix_connection(self.context.address, limit=MAX_FRAME)
                self._writers.add(writer)
                writer.write(frame)
                await writer.drain()
                try:
                    reply = await reader.readline()
                except ValueError as exc:
                    raise ProtocolError("Host reply exceeds frame limit") from exc
                if not reply.endswith(b"\n"):
                    raise ConnectionError("Host connection ended before a complete acknowledgement")
                return validate_response(decode_frame(reply))
        finally:
            if writer is not None:
                self._writers.discard(writer)
                writer.close()
                try:
                    async with asyncio.timeout(self.timeout):
                        await writer.wait_closed()
                except (OSError, TimeoutError):
                    pass

    async def aclose(self) -> None:
        self._closed = True
        writers = tuple(self._writers)
        for writer in writers:
            writer.close()
        if writers:
            async with asyncio.timeout(self.timeout):
                await asyncio.gather(*(writer.wait_closed() for writer in writers), return_exceptions=True)
        self._writers.clear()
