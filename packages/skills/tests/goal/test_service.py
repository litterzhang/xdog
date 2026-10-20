"""Explicit Goal work uses real persisted facts, never a hidden Agent planner."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from xdog.skills.goal.models import Owner, Report, report_digest
from xdog.skills.goal.store import GoalStore

from packages.skills.tests.goal.support import Host


@asynccontextmanager
async def setup(tmp_path):
    from xdog.skills.goal.host import OperationContext
    from xdog.skills.goal.service import GoalService

    owner = Owner("host-owner:namespace", tmp_path)
    store = await GoalStore.open(tmp_path / "private" / "goal.sqlite3", owner)
    host = Host(owner)
    service = GoalService(store, host)
    try:
        yield service, host, OperationContext(True)
    finally:
        if host.start_release is not None:
            host.start_release.set()
        if host.enqueue_release is not None:
            host.enqueue_release.set()
        await service.close()


async def goal_task(service, context):
    goal = await service.create("Outcome", "Produce evidence", ("Actual result is correct",), context=context)
    goal = await service.add_task(goal.id, "Implement")
    return goal, goal.tasks[0]


async def assigned(service, context):
    goal, task = await goal_task(service, context)
    goal = await service.assign(goal.id, task.id, "Inspect files and implement", access="read-only")
    return goal, goal.tasks[0]


async def test_optional_capability_is_not_delegation_or_automatic_model_work(tmp_path):
    from xdog.skills.goal.host import OperationContext

    async with setup(tmp_path) as (service, host, human):
        with pytest.raises(ValueError, match="user"):
            await service.create("No", "No delegation", ("Check",), context=OperationContext(False))
        goal = await service.create("Outcome", "Explicit delegated work", ("Inspect actual work",), context=human)
        assert (await service.status(goal.id)).status == "active"
        await service.reconcile(goal.id)
        assert host.started == [] and host.reviewed == [] and host.enqueued == []


async def test_assignment_report_acceptance_and_dependencies_are_distinct(tmp_path):
    async with setup(tmp_path) as (service, host, human):
        goal, task = await goal_task(service, human)
        goal = await service.add_task(goal.id, "Depends on implementation", depends_on=(task.id,))
        dependent = goal.tasks[-1]
        with pytest.raises(ValueError, match="depend"):
            await service.assign(goal.id, dependent.id, "not ready")

        async def check_start(spec):
            saved = await service.status(goal.id)
            assert saved.tasks[0].state == "starting"
            assert saved.tasks[0].attempts[-1].id == spec.attempt_id

        host.before_start = check_start
        goal = await service.assign(goal.id, task.id, "Actual work", access="read-only")
        current = goal.tasks[0].attempts[-1]
        assert goal.tasks[0].state == "running" and current.worker == host.started[0][1]
        report = Report(task.id, current.id, 1, "submitted", "Ready for acceptance", ("result.txt",), ())
        reporter = service.bound_reporter(goal.id, task.id, current.id)
        goal = await reporter.report(report)
        assert goal.tasks[0].state == "submitted" and goal.status != "completed"
        assert goal.tasks[0].acceptance is None
        with pytest.raises(ValueError, match="running|settling"):
            await service.accept_task(goal.id, task.id, current.id, 1, report_digest(report), "Accepted")
        host.finish(current.worker)
        with pytest.raises(ValueError, match="digest|report"):
            await service.accept_task(goal.id, task.id, current.id, 1, "0" * 64, "stale selection")
        goal = await service.accept_task(
            goal.id, task.id, current.id, 1, report_digest(report), "Actual evidence inspected"
        )
        assert goal.tasks[0].state == "accepted" and goal.status != "completed"
        host.before_start = None
        await service.assign(goal.id, dependent.id, "Now ready")
        assert len(host.started) == 2 and host.reviewed == []


async def test_bound_reports_reject_cross_attempt_and_conflicting_duplicate_revision(tmp_path):
    async with setup(tmp_path) as (service, host, human):
        goal, task = await assigned(service, human)
        attempt = task.attempts[-1]
        reporter = service.bound_reporter(goal.id, task.id, attempt.id)
        report = Report(task.id, attempt.id, 1, "submitted", "Proposed", ("result",), ())
        with pytest.raises(ValueError, match="bound|scope"):
            await reporter.report(replace(report, attempt_id="other-attempt"))
        first = await reporter.report(report)
        assert (await reporter.report(report)).version == first.version
        with pytest.raises(ValueError, match="revision|conflict"):
            await reporter.report(replace(report, summary="changed same revision"))
        host.finish(attempt.worker)
        accepted = await service.accept_task(goal.id, task.id, attempt.id, 1, report_digest(report), "Accepted")
        assert (await reporter.report(report)).version == accepted.version
        with pytest.raises(ValueError):
            await reporter.report(replace(report, revision=2, summary="late replacement"))
        assert (await service.status(goal.id)).tasks[0].acceptance.report_digest == report_digest(report)


async def test_human_pause_and_criteria_authority_are_not_source_labels(tmp_path):
    from xdog.skills.goal.host import OperationContext

    async with setup(tmp_path) as (service, host, human):
        goal, task = await assigned(service, human)
        automatic = OperationContext(False)
        for context in (automatic, {"user_authorized": True}):
            with pytest.raises(ValueError, match="user"):
                await service.revise(goal.id, "Changed", ("Forged",), "source says human", context=context)
        paused = await service.pause(goal.id, "User pause", context=human)
        assert paused.status == "paused" and host.cancelled == []
        with pytest.raises(ValueError, match="user"):
            await service.resume(goal.id, context=automatic)
        resumed = await service.resume(goal.id, context=human)
        changed = await service.revise(
            goal.id, "Updated outcome", ("Updated condition",), "User changed scope", context=human
        )
        assert resumed.status == "active" and changed.criteria_revision == 2
        assert host.cancelled == [] and len(host.started) == 1


async def test_goal_and_notification_stop_do_not_implicitly_cancel_a_worker(tmp_path):
    async with setup(tmp_path) as (service, host, human):
        goal, task = await assigned(service, human)
        worker = task.attempts[-1].worker
        await service.wait(goal.id, "Wait for actual result")
        await service.cancel_notifications(goal.id)
        abandoned = await service.abandon(goal.id, "No longer needed")
        assert abandoned.status == "abandoned" and not abandoned.notifications_enabled
        assert host.cancelled == []
        cancelled = await service.cancel_task(goal.id, task.id, "Explicitly stop this worker")
        assert host.cancelled == [worker] and cancelled.tasks[0].state == "cancelled"
        assert cancelled.status == "abandoned"


async def test_owned_start_settles_after_caller_cancellation_without_duplicate_worker(tmp_path):
    async with setup(tmp_path) as (service, host, human):
        goal, task = await goal_task(service, human)
        host.start_release = asyncio.Event()
        assigning = asyncio.create_task(service.assign(goal.id, task.id, "Start once"))
        await asyncio.wait_for(host.start_entered.wait(), 3)
        assigning.cancel()
        host.start_release.set()
        with pytest.raises(asyncio.CancelledError):
            await assigning
        saved = await service.status(goal.id)
        assert saved.tasks[0].state == "running" and len(host.started) == 1
        await service.reconcile(goal.id)
        assert len(host.started) == 1 and host.reviewed == []


async def test_cancel_during_start_records_intent_and_stops_exact_late_reference(tmp_path):
    async with setup(tmp_path) as (service, host, human):
        goal, task = await goal_task(service, human)
        host.start_release = asyncio.Event()
        assigning = asyncio.create_task(service.assign(goal.id, task.id, "Start once"))
        await asyncio.wait_for(host.start_entered.wait(), 3)
        await service.cancel_task(goal.id, task.id, "Explicit cancel while starting")
        host.start_release.set()
        await asyncio.wait_for(assigning, 3)
        saved = await service.status(goal.id)
        assert saved.tasks[0].state == "cancelled" and host.cancelled == [host.started[0][1]]


async def test_report_during_start_binding_preserves_reported_state(tmp_path):
    async with setup(tmp_path) as (service, host, human):
        goal, task = await goal_task(service, human)

        async def early_report(spec):
            report = Report(task.id, spec.attempt_id, 1, "submitted", "Fast result", ("evidence",), ())
            await service.bound_reporter(goal.id, task.id, spec.attempt_id).report(report)

        host.before_start = early_report
        saved = await service.assign(goal.id, task.id, "Fast worker")
        assert saved.tasks[0].state == "submitted" and saved.tasks[0].attempts[-1].worker == host.started[0][1]
        assert len(saved.tasks[0].attempts[-1].reports) == 1


async def test_concurrent_reports_do_not_lose_another_task_or_spawn_hidden_review(tmp_path):
    async with setup(tmp_path) as (service, host, human):
        goal, task = await goal_task(service, human)
        goal = await service.add_task(goal.id, "Second task")
        for row in goal.tasks:
            await service.assign(goal.id, row.id, "Work")
        goal = await service.status(goal.id)
        await asyncio.gather(
            *(
                service.bound_reporter(goal.id, row.id, row.attempts[-1].id).report(
                    Report(row.id, row.attempts[-1].id, 1, "submitted", "Done", ("evidence",), ())
                )
                for row in goal.tasks
            )
        )
        saved = await service.status(goal.id)
        assert [row.state for row in saved.tasks] == ["submitted", "submitted"]
        assert len(host.started) == 2 and host.reviewed == []


async def test_child_terminal_fact_notifies_once_without_acceptance_or_restart(tmp_path):
    async with setup(tmp_path) as (service, host, human):
        goal, task = await assigned(service, human)
        host.finish(task.attempts[-1].worker, "failed")
        saved = await service.reconcile(goal.id)
        assert saved.tasks[0].state == "interrupted" and saved.status != "completed"
        notices = await service.store.notices()
        repeated = await service.reconcile(goal.id)
        assert repeated.version == saved.version and await service.store.notices() == notices
        assert len(host.started) == 1 and host.reviewed == []


async def test_automatic_wait_cannot_turn_user_pause_into_an_automatic_resume(tmp_path):
    from xdog.skills.goal.host import OperationContext

    async with setup(tmp_path) as (service, host, human):
        goal, task = await goal_task(service, human)
        await service.pause(goal.id, "User paused", context=human)
        with pytest.raises(ValueError, match="paused|user"):
            await service.wait(goal.id, "Automatic event trying to release pause")
        with pytest.raises(ValueError, match="user"):
            await service.resume(goal.id, context=OperationContext(False))
        assert (await service.status(goal.id)).status == "paused"


async def test_actual_worker_binding_survives_a_lost_host_start_ack_without_respawn(tmp_path):
    from xdog.skills.goal.models import WorkerRef

    async with setup(tmp_path) as (service, host, human):
        goal, task = await goal_task(service, human)

        async def accepted_without_ack(spec):
            ref = WorkerRef("actual-worker", "actual-execution")
            await service.bind_worker(goal.id, task.id, spec.attempt_id, ref)
            from xdog.skills.goal.host import ChildStatus

            host.children[ref] = ChildStatus(ref, "running")
            host.started.append((spec, ref))
            raise ConnectionError("start accepted and bound before lost ACK")

        host.start_worker = accepted_without_ack
        with pytest.raises(ConnectionError):
            await service.assign(goal.id, task.id, "Run once")
        saved = await service.status(goal.id)
        assert saved.tasks[0].state == "running" and saved.tasks[0].attempts[-1].worker.id == "actual-worker"
        await service.reconcile(goal.id)
        with pytest.raises(ValueError):
            await service.assign(goal.id, task.id, "Do not duplicate")
        assert len(host.started) == 1


async def test_unknown_start_without_binding_is_interrupted_and_never_automatically_retried(tmp_path):
    async with setup(tmp_path) as (service, host, human):
        goal, task = await goal_task(service, human)
        calls = []

        async def unknown(spec):
            calls.append(spec)
            raise ConnectionError("start may have been accepted, no actual reference returned")

        host.start_worker = unknown
        with pytest.raises(ConnectionError):
            await service.assign(goal.id, task.id, "Start once")
        saved = await service.status(goal.id)
        assert saved.tasks[0].state == "interrupted" and saved.tasks[0].attempts[-1].worker is None
        await service.reconcile(goal.id)
        await service.status(goal.id)
        assert len(calls) == 1 and host.reviewed == []


async def test_owner_change_refuses_operations_without_mutating_goal_storage(tmp_path):
    async with setup(tmp_path) as (service, host, human):
        goal, task = await goal_task(service, human)
        before = await service.store.get(goal.id)
        host.owner = Owner("different-scope", tmp_path)
        with pytest.raises(ValueError, match="owner"):
            await service.add_task(goal.id, "must not be written")
        assert await service.store.get(goal.id) == before
