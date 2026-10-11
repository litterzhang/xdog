"""Bounded, non-destructive stopped-child image copy. No tools, files/URIs or model work."""
from __future__ import annotations

import asyncio
import base64
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from xdog.agent.input_queue import QueuedInput
from xdog.agent.supervision_images import images_to_dicts, input_receipt, validate_images
from xdog.ai.utils.image_files import decode_image

T = TypeVar('T')


async def settled_image_work(work: Callable[[], T]) -> T:
    """Cancellation revokes delivery, but waits for the actual owned thread to exit.

    Python cannot forcibly stop an executing thread. Do not release a caller's
    capacity slot or allow another copy to replace it while decode/hash still runs.
    Worker failure during cancellation is consumed; cancellation remains the result.
    """
    task = asyncio.create_task(asyncio.to_thread(work))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()  # Retrieve a late worker failure without publishing a receipt.
        raise


async def prepare_recovery(row: QueuedInput) -> dict[str, Any]:
    def prepare() -> dict[str, Any]:
        validate_images(row.images)
        receipt = input_receipt(row.content, row.images)
        return {'images': images_to_dicts(row.images), 'receipt': receipt}
    return await settled_image_work(prepare)


async def send_recovery(payload: dict[str, Any], scope: dict[str, str],
                        send: Callable[[dict[str, Any]], Awaitable[None]],
                        alive: Callable[[], bool | Awaitable[bool]]) -> None:
    """Stream only this immutable copy; caller retains its original durable input."""
    def decode() -> list[bytes]:
        return [decode_image(row['data'], row['mime_type'], max_bytes=10 * 1024 * 1024).data
                for row in payload['images']]
    bodies = await settled_image_work(decode)
    receipt = payload['receipt']
    manifest = [{'mime_type': mime, 'sha256': digest, 'bytes': len(body)} for mime, digest, body in zip(
        receipt['image_mime_types'], receipt['image_sha256'], bodies, strict=True)]
    async def write(kind: str, **fields: Any) -> None:
        current = alive()
        if not (await current if inspect.isawaitable(current) else current):
            raise ValueError('Child image recovery owner/queue changed; original not copied or restarted')
        await send({'type': 'child_recovery_' + kind, **scope, **fields})
    async with asyncio.timeout(10):
        await write('begin', images=manifest, receipt=receipt)
        for image_index, body in enumerate(bodies):
            for chunk_index, offset in enumerate(range(0, len(body), 64 * 1024)):
                await write('chunk', image_index=image_index, index=chunk_index,
                            data=base64.b64encode(body[offset:offset + 64 * 1024]).decode('ascii'))
        await write('end')
