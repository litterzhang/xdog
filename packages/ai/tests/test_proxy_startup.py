"""Proxy availability must not depend on upstream connectivity."""
import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import xdog.ai as ai
from xdog.ai import proxy
from xdog.ai.providers.runtime import Runtime
from xdog.ai.types import Model


def runtime_with(*providers):
    runtime = Runtime()
    runtime._active = {provider.id: provider for provider in providers}
    return runtime


@pytest.mark.parametrize("cached", [True, False])
async def test_listens_with_pending_sync_and_cleans_up(monkeypatch, cached):
    """Real loopback request, but no provider requests or real credentials."""
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def slow_sync():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    models = (Model(id="copilot/test", name="Test"),) if cached else ()
    runtime = runtime_with(SimpleNamespace(id="copilot", sync_models=slow_sync, models=lambda: models))
    monkeypatch.setattr(ai, "load", lambda: runtime)
    real_start_server = asyncio.start_server
    bound = asyncio.get_running_loop().create_future()

    async def capture_server(*args, **kwargs):
        server = await real_start_server(*args, **kwargs)
        bound.set_result(server)
        return server

    monkeypatch.setattr(asyncio, "start_server", capture_server)
    task = asyncio.create_task(proxy.start_proxy(port=0))
    try:
        server = await asyncio.wait_for(asyncio.shield(bound), 2)
        await asyncio.wait_for(started.wait(), 2)
        port = server.sockets[0].getsockname()[1]
        async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
            health = await client.get(f"http://127.0.0.1:{port}/health")
            response = await client.get(f"http://127.0.0.1:{port}/v1/models")
        assert health.json() == {"status": "ok"}
        assert response.status_code == 200
        assert [m["id"] for m in response.json()["data"]] == (["copilot/test"] if cached else [])
        assert not task.done()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    assert stopped.is_set()
    assert not server.is_serving()


async def test_refresh_failure_does_not_skip_other_provider(caplog):
    failing = AsyncMock(side_effect=httpx.ConnectTimeout("secret-bearing URL"))
    healthy = AsyncMock(return_value=(Model(id="copilot/test"),))
    runtime = runtime_with(
        SimpleNamespace(id="antigravity", sync_models=failing),
        SimpleNamespace(id="copilot", sync_models=healthy),
    )
    await proxy._refresh_proxy_models(runtime)
    healthy.assert_awaited_once()
    assert "antigravity" in caplog.text
    assert "ConnectTimeout" in caplog.text
    assert "secret-bearing" not in caplog.text


async def test_refresh_is_concurrent_bounded_and_cancels_timeout(monkeypatch, caplog):
    completed = asyncio.Event()
    cancelled = asyncio.Event()

    async def stalled():
        try:
            await completed.wait()
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def healthy():
        completed.set()
        return ()

    monkeypatch.setattr(proxy, "_MODEL_SYNC_TIMEOUT", 0.1)
    runtime = runtime_with(
        SimpleNamespace(id="stalled", sync_models=stalled),
        SimpleNamespace(id="healthy", sync_models=healthy),
    )
    await asyncio.wait_for(proxy._refresh_proxy_models(runtime), 2)
    assert completed.is_set()
    assert cancelled.is_set()
    assert "TimeoutError" in caplog.text


async def test_no_active_provider_does_not_bind(monkeypatch):
    monkeypatch.setattr(ai, "load", lambda: Runtime())
    start = AsyncMock()
    monkeypatch.setattr(asyncio, "start_server", start)
    with pytest.raises(RuntimeError, match="No active providers"):
        await proxy.start_proxy(port=0)
    start.assert_not_awaited()


async def test_bind_failure_does_not_start_upstream_refresh(monkeypatch):
    sync = AsyncMock()
    monkeypatch.setattr(ai, "load", lambda: runtime_with(SimpleNamespace(id="copilot", sync_models=sync)))
    monkeypatch.setattr(asyncio, "start_server", AsyncMock(side_effect=OSError("address in use")))
    with pytest.raises(OSError, match="address in use"):
        await proxy.start_proxy(port=0)
    sync.assert_not_awaited()
