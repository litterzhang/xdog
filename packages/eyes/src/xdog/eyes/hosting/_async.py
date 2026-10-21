"""Settle feature-owned work before releasing journal/connection ownership."""
from __future__ import annotations

import asyncio
from typing import TypeVar

T = TypeVar('T')


async def wait_owned(future: asyncio.Future[T]) -> T:
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
    result = future.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
