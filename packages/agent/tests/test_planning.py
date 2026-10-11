from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.agent.planning import PlanOwner, PlanState

STEPS = [{"description": "Inspect files", "status": "pending"}, {"description": "Apply change", "status": "pending"}]


async def test_structured_plan_tool_is_revision_bound_and_cannot_authorize_execution():
    saved = []
    owner = PlanOwner(PlanState(), saved.append)
    owner.change("start", 0, "Implement safely")
    tool = owner.tool()
    result = await tool.execute("one", {"expected_revision": 1, "steps": STEPS})
    assert owner.state.mode == "planning" and owner.state.revision == 2 and len(saved) == 2
    assert "planning" in result.content[0].text
    with pytest.raises(ValueError, match="cannot change"):
        await tool.execute("bad", {"expected_revision": 2, "steps": STEPS, "mode": "executing"})
    with pytest.raises(ValueError, match="changed"):
        owner.change("execute", 1)
    assert owner.state.mode == "planning"
    owner.change("execute", 2)
    assert owner.state.mode == "executing" and owner.state.revision == 3
    owner.change("revise", 3)
    assert owner.state.mode == "planning"
    owner.close()
    with pytest.raises(RuntimeError, match="closed"):
        owner.change("stop", 4)


async def test_parallel_metadata_updates_only_accept_one_revision():
    owner = PlanOwner(PlanState(), lambda state: None)
    owner.change("start", 0, "Objective")
    results = await asyncio.gather(*(owner.tool().execute(str(i), {"expected_revision": 1, "steps": STEPS})
                                     for i in range(2)), return_exceptions=True)
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert owner.state.revision == 2


async def test_cancel_settles_persistence_before_releasing_tool_ownership():
    entered, release = threading.Event(), threading.Event()
    def persist(state):
        entered.set()
        assert release.wait(3)
    owner = PlanOwner(PlanState(mode="planning", revision=1, objective="Objective"), persist)
    task = asyncio.create_task(owner.tool().execute("one", {"expected_revision": 1, "steps": STEPS}))
    async with asyncio.timeout(3):
        while not entered.is_set():
            await asyncio.sleep(0.002)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert owner.state.revision == 2  # Cancellation cannot roll back a committed metadata write.


def test_failed_persistence_retains_previous_state():
    owner = PlanOwner(PlanState(), lambda state: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        owner.change("start", 0, "Objective")
    assert owner.state == PlanState()


@pytest.mark.parametrize("bad", [
    {"mode": "other", "revision": 0}, {"mode": [], "revision": 0}, {"mode": "planning", "revision": True},
    {"mode": "planning", "revision": 1, "objective": ""},
    {"mode": "planning", "revision": 1, "objective": "x", "steps": [{"description": "x", "status": "wrong"}]},
])
def test_invalid_persistence_fails_closed_instead_of_disabling_plan(bad):
    with pytest.raises(ValueError):
        PlanState.from_dict(bad)
