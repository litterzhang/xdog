"""Gateway Goal work wakes on state changes, not a model/timer polling loop."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.gateway import GatewayServer


async def cleanup(server, task):
    server._shutdown_event.set()
    task.cancel()
    await asyncio.wait_for(task, 1)


async def test_startup_tick_is_immediate_and_idle_does_not_poll_model():
    server = GatewayServer(ClawConfig())
    entered = asyncio.Event()
    tick = AsyncMock(side_effect=lambda: entered.set())
    server._orchestrator = SimpleNamespace(tick=tick)
    task = asyncio.create_task(server._goal_loop())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert server._GOAL_RECONCILE_INTERVAL == 30
        await asyncio.sleep(.01)
        assert tick.await_count == 1
        # Hundreds of synchronous notifications are one actionable wake.
        entered.clear()
        for _ in range(200):
            server._goal_signal.set()
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(.01)
        assert tick.await_count == 2
    finally:
        await cleanup(server, task)


async def test_notifications_during_work_coalesce_and_do_not_busy_spin():
    server = GatewayServer(ClawConfig())
    first, next_pass, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    count, active, peak = 0, 0, 0
    async def tick():
        nonlocal count, active, peak
        count += 1
        active += 1
        peak = max(peak, active)
        try:
            if count == 1:
                first.set()
                await release.wait()
            else:
                next_pass.set()
        finally:
            active -= 1
    server._orchestrator = SimpleNamespace(tick=tick)
    task = asyncio.create_task(server._goal_loop())
    try:
        await asyncio.wait_for(first.wait(), 1)
        for _ in range(200):
            server._goal_signal.set()
            await asyncio.sleep(0)
        assert count == 1
        release.set()
        await asyncio.wait_for(next_pass.wait(), 1)
        await asyncio.sleep(.01)
        assert count == 2 and peak == 1
    finally:
        release.set()
        await cleanup(server, task)


async def test_unchanged_obligations_reconcile_after_timeout_without_external_wake(monkeypatch):
    server = GatewayServer(ClawConfig())
    monkeypatch.setattr(server, '_GOAL_RECONCILE_INTERVAL', .01)
    reconciled = asyncio.Event()
    count = 0
    async def tick():
        nonlocal count
        count += 1
        if count == 2:
            server._shutdown_event.set()
            reconciled.set()
    server._orchestrator = SimpleNamespace(tick=tick)
    task = asyncio.create_task(server._goal_loop())
    try:
        await asyncio.wait_for(reconciled.wait(), 1)
        await asyncio.wait_for(task, 1)
        assert count == 2
    finally:
        await cleanup(server, task)


async def test_tick_failure_waits_for_event_or_fallback_and_cancellation_settles():
    server = GatewayServer(ClawConfig())
    failed, recovered = asyncio.Event(), asyncio.Event()
    count = 0
    async def tick():
        nonlocal count
        count += 1
        if count == 1:
            failed.set()
            raise OSError('Transient durable read failure')
        recovered.set()
    server._orchestrator = SimpleNamespace(tick=tick)
    task = asyncio.create_task(server._goal_loop())
    try:
        await asyncio.wait_for(failed.wait(), 1)
        await asyncio.sleep(.01)
        assert count == 1
        server._goal_signal.set()
        await asyncio.wait_for(recovered.wait(), 1)
        assert count == 2
    finally:
        await cleanup(server, task)
    assert task.done() and not server._goal_signal.is_set()


async def test_shutdown_signal_cannot_admit_another_tick():
    server = GatewayServer(ClawConfig())
    entered, release = asyncio.Event(), asyncio.Event()
    async def tick():
        entered.set()
        await release.wait()
    work = AsyncMock(side_effect=tick)
    server._orchestrator = SimpleNamespace(tick=work)
    task = asyncio.create_task(server._goal_loop())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        server._shutdown_event.set()
        server._goal_signal.set()
        release.set()
        await asyncio.wait_for(task, 1)
        assert work.await_count == 1
    finally:
        release.set()
        await cleanup(server, task)


async def test_gateway_binds_wake_before_registration_and_replaces_it_on_restart(tmp_path, monkeypatch):
    from packages.claw.tests.test_gateway_shutdown import make_server
    server = make_server(tmp_path, monkeypatch)
    await server.start()
    try:
        orch = server._orchestrator
        assert orch._goal_wake == server._goal_signal.set
        signal = Mock()
        monkeypatch.setattr(orch, '_goal_wake', signal)
        # Dynamic registration callback must consult the current host hook;
        # no stale captured Event.set and no model invocation is required.
        runtime = next(iter(orch._runtimes.values()))
        runtime.goal_manager._wake_fn()
        signal.assert_called_once_with()
        await server.stop()
        await server.start()
        assert server._orchestrator is not orch
        assert server._orchestrator._goal_wake == server._goal_signal.set
    finally:
        await server.stop()
