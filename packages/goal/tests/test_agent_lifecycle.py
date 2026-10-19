"""Portable Goal uses existing actual Agent/Supervisor execution, not another loop."""

from __future__ import annotations

from xdog.goal.host import OperationContext
from xdog.goal.models import Owner, report_digest
from xdog.goal.service import GoalService
from xdog.goal.store import GoalStore

from packages.goal.tests.agent_host import AgentHost


async def test_actual_supervised_worker_report_and_independent_reviewer_complete(tmp_path):
    tmp_path.joinpath("result.txt").write_text("actual result")
    owner = Owner("standalone-host:namespace", tmp_path)
    host = AgentHost(owner)
    service = GoalService(await GoalStore.open(tmp_path / "private" / "goals.sqlite3", owner), host)
    host.service = service
    try:
        goal = await service.create(
            "Portable", "Inspect actual result", ("Actual result is present",), context=OperationContext(True)
        )
        goal = await service.add_task(goal.id, "Read/report actual result")
        task_id = goal.tasks[0].id
        goal = await service.assign(goal.id, task_id, "Read actual evidence")
        await host.wait(goal.tasks[0].attempts[-1].worker)
        goal = await service.status(goal.id)
        attempt = goal.tasks[0].attempts[-1]
        assert goal.tasks[0].state == "submitted" and goal.status != "completed"
        assert attempt.reports[-1].evidence == ("result.txt",)
        goal = await service.accept_task(
            goal.id, task_id, attempt.id, 1, report_digest(attempt.reports[-1]), "Inspected"
        )
        goal = await service.request_complete(goal.id, paths=("result.txt",))
        await host.wait(goal.review.worker)
        completed = await service.reconcile(goal.id)
        assert completed.status == "completed"
        assert len(host.agent_ids) == 2 and len(set(host.agent_ids)) == 2
        assert [role for role, number in host.model_calls] == ["worker", "worker", "worker", "reviewer", "reviewer"]
        await service.drain_notifications()
        assert host.accepted and all(row.status == "accepted" for row in await service.store.notices())
    finally:
        await host.close()
        await service.close()
