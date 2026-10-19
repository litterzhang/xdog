"""Host dispatch is per group, not another Agent loop or busy continuation timer."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput


@pytest.fixture
async def orchestrator(tmp_path, monkeypatch):
    def create(group, data_dir, **kwargs):
        return GroupRuntime(group=group, data_dir=data_dir, workspace_dir=tmp_path / group.id)
    monkeypatch.setattr(GroupRuntime, 'create', create)
    orch = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')))
    for group in ('slow', 'fast'):
        orch.register_group(Group(id=group))
        orch._runtimes[group].goal_manager.create(group, 'Outcome', 'Produce evidence', ['Inspect result'], [])
    try:
        yield orch
    finally:
        await orch.stop()


async def test_slow_goal_turn_cannot_starve_another_group_or_duplicate_owner(orchestrator, monkeypatch):
    orch = orchestrator
    slow_started, fast_done, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    async def slow(**kwargs):
        calls.append('slow')
        slow_started.set()
        await stop.wait()
    async def fast(**kwargs):
        fast_done.set()
    monkeypatch.setattr(orch._runtimes['slow'].goal_manager, 'tick', slow)
    monkeypatch.setattr(orch._runtimes['fast'].goal_manager, 'tick', fast)
    await orch.tick()
    async with asyncio.timeout(1):
        await slow_started.wait()
        await fast_done.wait()
    await orch.tick()
    assert calls == ['slow']
    await orch.stop()
    assert not orch._goal_jobs


async def test_queued_user_work_prevents_automatic_goal_dispatch(orchestrator, monkeypatch):
    orch = orchestrator
    tick = AsyncMock()
    monkeypatch.setattr(orch._runtimes['slow'].goal_manager, 'tick', tick)
    orch._queue._queues['slow'] = [UserInput(group_id='slow', content='user first')]
    await orch.tick()
    tick.assert_not_awaited()
    assert 'slow' not in orch._goal_jobs


async def test_unchanged_waiting_review_does_not_wake_into_busy_spin(orchestrator, monkeypatch):
    orch = orchestrator
    wake = Mock()
    orch._goal_wake = wake
    for runtime in orch._runtimes.values():
        goal = runtime.goal_manager.tracker.list_goals()[0]
        runtime.goal_manager._put(goal, phase='review_requested')
        monkeypatch.setattr(runtime.goal_manager, 'tick', AsyncMock())
    wake.reset_mock()
    await orch.tick()
    await asyncio.sleep(0)
    assert not orch._goal_jobs
    wake.assert_not_called()
