"""Real Goal-owned readonly projection and the actual packaged display worker."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.supervision import settle_on

from packages.skills.tests.goal.test_service import setup


async def test_projection_pages_actual_goals_without_history_notices_views_or_execution_mutation(tmp_path):
    from xdog.skills.goal.ui_views import GoalView

    async with setup(tmp_path) as (service, host, human):
        goals = [await service.create(f"Title {i}", "Read actual state", ("Check",), context=human) for i in range(3)]
        before = [await service.store.history(goal.id) for goal in goals]
        notices = await service.store.notices()
        view = GoalView(service)
        first = await view.read({"limit": 2})
        second = await view.read({"offset": 2, "limit": 2})
        assert first["format"] == "xdog.goal_view.v1" and first["total"] == 3
        assert len(first["items"]) == 2 and first["next_offset"] == 2
        assert len(second["items"]) == 1 and second["next_offset"] is None
        assert {item["id"] for part in (first, second) for item in part["items"]} == {g.id for g in goals}
        assert await view.read({"limit": 2}) == first
        assert [await service.store.history(goal.id) for goal in goals] == before
        assert await service.store.notices() == notices
        assert not host.started and not host.reviewed and not host.enqueued


@pytest.mark.parametrize("query", [
    {"action": "complete"}, {"owner": "other"}, {"path": "/private"}, {"method": "create"},
    {"offset": True}, {"offset": -1}, {"limit": True}, {"limit": 0}, {"limit": 33}, {"goal_id": "../other"},
])
async def test_projection_cannot_write_choose_an_owner_or_coerce_its_query(tmp_path, query):
    from xdog.skills.goal.ui_views import GoalView

    async with setup(tmp_path) as (service, host, _):
        with pytest.raises(ValueError):
            await GoalView(service).read(query)
        assert not await service.list() and not host.started


async def test_projection_preserves_true_submission_and_marks_bounded_utf8_previews(tmp_path):
    from xdog.skills.goal.models import Report
    from xdog.skills.goal.ui_views import GoalView

    async with setup(tmp_path) as (service, host, human):
        goal = await service.create("海" * 500, "Outcome" * 10000, ("original criterion",), context=human)
        goal = await service.add_task(goal.id, "Unaccepted actual task")
        goal = await service.assign(goal.id, goal.tasks[0].id, "Explicit domain assignment")
        task = goal.tasks[0]
        (tmp_path / "result.txt").write_text("Actual proposal evidence")
        await service.report(goal.id, Report(
            task.id, task.attempts[-1].id, 1, "submitted", "Proposal only", ("result.txt",)))
        before_starts = list(host.started)
        snapshot = await GoalView(service).read({"goal_id": goal.id})
        row = snapshot["items"][0]
        assert row["status"] == "active" and row["tasks"]["accepted"] == 0 and row["tasks"]["total"] == 1
        assert row["tasks"]["submitted"] == 1 and host.started == before_starts
        assert row["criteria_count"] == 1 and {"title", "outcome"} <= set(row["preview_fields"])
        assert len(json.dumps(snapshot, ensure_ascii=False).encode()) < 65536
        assert (await service.status(goal.id)).title == "海" * 500


async def test_projection_refuses_changed_owner_instead_of_fake_empty_data(tmp_path):
    from xdog.skills.goal.models import Owner
    from xdog.skills.goal.ui_views import GoalView

    async with setup(tmp_path) as (service, host, human):
        await service.create("Private", "Original owner", ("Check",), context=human)
        view = GoalView(service)
        host.owner = Owner("foreign", tmp_path)
        with pytest.raises(ValueError, match="owner|Owner"):
            await view.read({})


async def test_same_packaged_goal_worker_uses_view_port_without_ui_or_a_business_host_loop(tmp_path):
    from xdog.skills.goal.ui_views import GoalView

    async with setup(tmp_path) as (service, host, human):
        goal = await service.create("Actual goal", "Inspect only", ("Check",), context=human)
        parent_loop = asyncio.get_running_loop()
        view = GoalView(service)
        runtime = ExtensionRuntime(("goal_ui",), tmp_path / "no-copy", tmp_path,
                                   context=lambda: {"session_id": "generic-goal-view"})

        async def read(query):
            return await settle_on(parent_loop, view.read(query))

        try:
            runtime.views.bind("goal_ui", "goals", read, valid=lambda: True)
            result = await runtime.command("goals", "")
            assert goal.id in str(result) and "Actual goal" in str(result) and "active" in str(result)
            assert runtime.snapshot()["ui"]["goal_ui:goals"]["kind"] == "status"
            assert runtime.dialog_handler is None and not host.started and not host.reviewed
            await runtime.manage("unload", "goal_ui")
            assert not runtime.snapshot()["ui"] and (await service.status(goal.id)).status == "active"
        finally:
            await runtime.aclose()
        unavailable = ExtensionRuntime(("goal_ui",), tmp_path / "no-copy", tmp_path,
                                       context=lambda: {"session_id": "no-view-grant", "host_views": True})
        try:
            result = str(await unavailable.command("goals", ""))
            assert "unavailable" in result.lower() and "no goals" not in result.lower()
        finally:
            await unavailable.aclose()


async def test_packaged_worker_never_turns_a_malformed_snapshot_into_no_goals(tmp_path):
    runtime = ExtensionRuntime(("goal_ui",), tmp_path / "no-copy", tmp_path,
                               context=lambda: {"session_id": "invalid-display"})

    async def read(query):
        return {"format": "unknown", "items": [], "total": 0}

    try:
        runtime.views.bind("goal_ui", "goals", read, valid=lambda: True)
        result = str(await runtime.command("goals", ""))
        assert "unavailable" in result.lower() and "no goals" not in result.lower()
        assert runtime.snapshot()["ui"]["goal_ui:goals"]["lines"] == ["Goal view unavailable"]
    finally:
        await runtime.aclose()
