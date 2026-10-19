"""Aggregate Goal history is not a hidden task-count or lifetime-data budget."""
from __future__ import annotations

from dataclasses import replace

import pytest
from xdog.goal.models import Goal, Owner, Task
from xdog.goal.store import GoalStore


def large_goal(*, amount=160, width=8000):
    return Goal('large-goal', 'Long-running outcome', 'Retain exact work', ('Original criterion',),
        tasks=tuple(Task(f'task-{i}', f'Original task {i}: ' + 'x' * width) for i in range(amount)))


async def test_aggregate_goal_over_old_megabyte_cap_roundtrips_reopens_and_preserves_versions(tmp_path):
    owner = Owner('actual-sdk-owner', tmp_path)
    path = tmp_path / 'private' / 'goals.sqlite3'
    goal = large_goal()
    store = await GoalStore.open(path, owner)
    try:
        saved = await store.put(goal, expected_version=0)
        assert saved == goal and await store.get(goal.id) == goal
        await store.put(replace(goal, version=2, summary='Explicit later change'), expected_version=1)
        assert await store.at_version(goal.id, 1) == goal
    finally:
        await store.close()
    reopened = await GoalStore.open(path, owner)
    try:
        assert len((await reopened.get(goal.id)).tasks) == 160
        assert await reopened.at_version(goal.id, 1) == goal
        assert (await reopened.get(goal.id)).summary == 'Explicit later change'
    finally:
        await reopened.close()


def test_large_valid_dependency_graph_does_not_depend_on_python_recursion_depth():
    count = 1500
    tasks = tuple(Task(f'task-{i}', 'Actual independent task',
                       depends_on=(f'task-{i - 1}',) if i else ()) for i in range(count))
    goal = Goal('large-dag', 'Ordered outcome', 'Check all work', ('Every task is preserved',), tasks=tasks[::-1])
    assert goal.tasks == tasks[::-1]
    with pytest.raises(ValueError, match='acyclic'):
        Goal('cycle', 'Cycle', 'Reject cycle', ('Check',), tasks=(
            Task('one', 'First', depends_on=('two',)), Task('two', 'Second', depends_on=('one',))))


async def test_cancelled_snapshot_preparation_settles_before_store_close_without_admitting_write(tmp_path, monkeypatch):
    import asyncio
    import threading

    from xdog.goal import store as module

    owner = Owner('actual-preparation-owner', tmp_path)
    path = tmp_path / 'private' / 'goals.sqlite3'
    store = await GoalStore.open(path, owner)
    entered, release = threading.Event(), threading.Event()
    original = module.encode_goal
    threads = []

    def actual_encode(goal):
        value = original(goal)
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        return value

    monkeypatch.setattr(module, 'encode_goal', actual_encode)
    work = asyncio.create_task(store.put(large_goal(), expected_version=0))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            work.cancel()
            await asyncio.sleep(0)
        closing = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        assert not work.done() and not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        await closing
        assert all(thread != threading.get_ident() for thread in threads)
    finally:
        release.set()
        await asyncio.gather(*(task for task in (work, closing) if task is not None), return_exceptions=True)
        await store.close()
    reopened = await GoalStore.open(path, owner)
    try:
        assert await reopened.get('large-goal') is None
    finally:
        await reopened.close()
