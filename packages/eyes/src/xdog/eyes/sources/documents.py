"""Finite document parsing on an owned worker, never a browser or code executor."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar
from urllib.parse import urljoin, urlsplit

T = TypeVar("T")
MAX_NODES = 100_000
MAX_DEPTH = 128


def link(value: str, base: str) -> str:
    target = urljoin(base, value.strip())
    parsed = urlsplit(target)
    if (
        not value.strip()
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or any(ord(char) < 33 or ord(char) == 127 for char in target)
    ):
        raise ValueError("Expected an HTTP(S) evidence link without userinfo or controls")
    return target


async def parse_owned(parse: Callable[[], T]) -> T:
    """Keep the daemon responsive; cancellation settles the actual bounded parser."""
    task = asyncio.create_task(asyncio.to_thread(parse))
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
            task.exception()
        raise
