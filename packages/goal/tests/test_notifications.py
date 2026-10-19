"""Goal notices are durable idempotent intake, not prompt replay or approval."""

from __future__ import annotations

import asyncio

from xdog.goal.models import Report

from packages.goal.tests.test_service import assigned, setup


async def notice(service, context):
    goal, task = await assigned(service, context)
    report = Report(task.id, task.attempts[-1].id, 1, "submitted", "Proposed result", ("result",), ())
    await service.bound_reporter(goal.id, task.id, report.attempt_id).report(report)
    return goal


async def test_ack_loss_recovers_original_enqueue_receipt_without_another_start_or_send(tmp_path):
    async with setup(tmp_path) as (service, host, human):
        goal = await notice(service, human)
        host.lose_enqueue_ack = True
        await service.drain_notifications()
        (first,) = await service.store.notices()
        assert first.status == "unknown" and len(host.enqueued) == 1
        await service.drain_notifications()
        (second,) = await service.store.notices()
        assert second.status == "accepted" and second.notice.id == first.notice.id
        assert len(host.enqueued) == 1 and len(host.started) == 1 and host.reviewed == []
        assert (await service.status(goal.id)).status != "completed"


async def test_notification_cancel_stops_pending_not_worker_or_already_accepted_input(tmp_path):
    async with setup(tmp_path) as (service, host, human):
        goal = await notice(service, human)
        await service.cancel_notifications(goal.id)
        assert (await service.store.notices())[0].status == "cancelled"
        await service.drain_notifications()
        assert host.enqueued == [] and host.cancelled == []
        saved = await service.status(goal.id)
        attempt = saved.tasks[0].attempts[-1]
        await service.bound_reporter(goal.id, saved.tasks[0].id, attempt.id).report(
            Report(saved.tasks[0].id, attempt.id, 2, "submitted", "Later result", ("later",), ())
        )
        assert len(await service.store.notices()) == 1


async def test_disabling_during_admitted_enqueue_does_not_retract_host_acceptance(tmp_path):
    async with setup(tmp_path) as (service, host, human):
        goal = await notice(service, human)
        host.enqueue_release = asyncio.Event()
        draining = asyncio.create_task(service.drain_notifications())
        await asyncio.wait_for(host.enqueue_entered.wait(), 3)
        await service.cancel_notifications(goal.id)
        host.enqueue_release.set()
        await asyncio.wait_for(draining, 3)
        assert (await service.store.notices())[0].status == "accepted"
        assert len(host.accepted) == 1 and host.cancelled == []


async def test_wrong_owner_receipt_cannot_mark_goal_notice_delivered(tmp_path):
    from xdog.goal.host import EnqueueReceipt

    async with setup(tmp_path) as (service, host, human):
        await notice(service, human)

        async def wrong_owner(value):
            return EnqueueReceipt("different-owner", value.id, "accepted")

        host.enqueue = wrong_owner
        await service.drain_notifications()
        (receipt,) = await service.store.notices()
        assert receipt.status == "unknown" and receipt.error
        assert host.cancelled == []
