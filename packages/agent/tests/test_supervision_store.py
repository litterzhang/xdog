from __future__ import annotations

import asyncio
import threading

import pytest


async def test_shared_mcp_request_cancellation_waits_for_actual_owner_settlement(tmp_path):
    from xdog.agent.mcp import McpRegistry

    entered, release = threading.Event(), threading.Event()

    async def request():
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            while not release.is_set():
                try:
                    await asyncio.sleep(0.002)
                except asyncio.CancelledError:
                    continue

    manager = McpRegistry({}, tmp_path)
    task = asyncio.create_task(manager._bridge(request()))
    try:
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.003)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await manager.aclose()
