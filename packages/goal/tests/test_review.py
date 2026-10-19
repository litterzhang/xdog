"""Only an explicit independent host execution and fresh real evidence can finish."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from xdog.goal.host import ChildStatus, OperationContext
from xdog.goal.models import Owner, WorkerRef
from xdog.goal.service import GoalService
from xdog.goal.store import GoalStore

from packages.goal.tests.support import Host


class ReviewHost(Host):
    async def start_reviewer(self, spec):
        assert spec.access == "read-only"
        self.reviewed.append(spec)
        ref = WorkerRef("reviewer-" + spec.review_id, "execution-" + spec.review_id)
        self.children[ref] = ChildStatus(ref, "running")
        return ref


@asynccontextmanager
async def setup_review(tmp_path):
    host = ReviewHost(Owner("owner", tmp_path))
    store = await GoalStore.open(tmp_path / "private" / "goals.sqlite3", host.owner)
    service = GoalService(store, host)
    goal = await service.create(
        "Outcome", "Deliver result", ("Result matches requirements",), context=OperationContext(True)
    )
    tmp_path.joinpath("result.txt").write_text("actual evidence")
    try:
        yield service, host, goal
    finally:
        await service.close()


def verdict(goal, status="passed"):
    return {
        "verdict": "passed" if status == "passed" else "continue",
        "summary": "Independent inspection",
        "conditions": [
            {"condition": condition, "status": status, "evidence": ["result.txt"], "reason": "Read actual result"}
            for condition in goal.criteria
        ],
    }


def finish(host, goal, value):
    host.children[goal.review.worker] = ChildStatus(goal.review.worker, "succeeded", json.dumps(value))


async def test_no_implicit_review_on_status_reconcile_or_work_then_explicit_review_once(tmp_path):
    async with setup_review(tmp_path) as (service, host, goal):
        for _ in range(3):
            await service.status(goal.id)
            await service.reconcile(goal.id)
        assert not host.reviewed
        requested = await service.request_complete(goal.id, paths=("result.txt",))
        repeated = await service.request_complete(goal.id, paths=("result.txt",))
        assert repeated.review.id == requested.review.id and len(host.reviewed) == 1
        assert requested.status == "reviewing" and requested.review.worker
        finish(host, requested, verdict(requested))
        completed = await service.reconcile(goal.id)
        assert completed.status == "completed" and completed.review.state == "passed"
        assert completed.review.findings[0].evidence == ("result.txt",)
        assert len(host.reviewed) == 1 and host.cancelled == []


@pytest.mark.parametrize(
    "fault",
    ["files", "criteria", "wrong_execution", "missing_condition", "no_evidence", "failed_condition", "script_result"],
)
async def test_stale_incomplete_or_script_verdict_cannot_finish_goal(tmp_path, fault):
    async with setup_review(tmp_path) as (service, host, goal):
        requested = await service.request_complete(goal.id, paths=("result.txt",))
        value = verdict(requested)
        if fault == "files":
            tmp_path.joinpath("result.txt").write_text("changed during review")
        elif fault == "criteria":
            await service.revise(
                goal.id, "New outcome", ("Different original criterion",), "User change", context=OperationContext(True)
            )
        elif fault == "wrong_execution":
            host.children[requested.review.worker] = ChildStatus(
                WorkerRef("forged", "other-execution"), "succeeded", json.dumps(value)
            )
        elif fault == "missing_condition":
            value["conditions"] = []
        elif fault == "no_evidence":
            value["conditions"][0]["evidence"] = []
        elif fault == "failed_condition":
            value["conditions"][0]["status"] = "failed"  # Overall passed cannot override a condition failure.
        elif fault == "script_result":
            value = {"exit_code": 0, "stdout": "passed"}
        if fault != "wrong_execution":
            finish(host, requested, value)
        try:
            saved = await service.reconcile(goal.id)
        except ValueError:
            saved = await service.status(goal.id)
        assert saved.status != "completed"
        assert len(host.reviewed) == 1, "Reconcile must not silently launch another reviewer"


async def test_revision_preserves_original_reviewer_for_explicit_cancellation(tmp_path):
    async with setup_review(tmp_path) as (service, host, goal):
        requested = await service.request_complete(goal.id, paths=("result.txt",))
        revised = await service.revise(
            goal.id, "Changed outcome", ("Changed condition",), "User revision", context=OperationContext(True)
        )
        assert revised.review.worker == requested.review.worker and revised.review.state == "invalid"
        assert host.cancelled == []
        await service.cancel_review(goal.id, "Explicitly stop obsolete review")
        assert host.cancelled == [requested.review.worker]
        assert (await service.status(goal.id)).status != "completed"


@pytest.mark.parametrize("path", ["../outside", "/etc/passwd", "symlink.txt"])
async def test_unsafe_evidence_paths_never_start_a_reviewer(tmp_path, path):
    async with setup_review(tmp_path) as (service, host, goal):
        tmp_path.joinpath("symlink.txt").symlink_to(tmp_path / "result.txt")
        with pytest.raises(ValueError):
            await service.request_complete(goal.id, paths=(path,))
        assert host.reviewed == []


async def test_goal_local_fingerprint_tracks_real_files_and_excludes_derived_runtime_views(tmp_path):
    from xdog.goal.evidence import fingerprint

    async with setup_review(tmp_path) as (service, host, goal):
        # The Goal journal in this test is selected explicitly out of the evidence set.
        one = await fingerprint(goal, tmp_path, ("result.txt",))
        tmp_path.joinpath(".xdog").mkdir()
        tmp_path.joinpath(".xdog", "view.json").write_text("derived status")
        assert await fingerprint(goal, tmp_path, ("result.txt",)) == one
        tmp_path.joinpath("result.txt").write_text("new bytes")
        assert await fingerprint(goal, tmp_path, ("result.txt",)) != one


async def test_review_start_commits_request_before_host_and_preserves_lost_ack_binding(tmp_path):
    async with setup_review(tmp_path) as (service, host, goal):

        async def started_but_ack_lost(spec):
            saved = await service.status(goal.id)
            assert saved.review.id == spec.review_id and saved.review.state == "starting"
            ref = WorkerRef("actual-reviewer", "actual-review-execution")
            await service.bind_reviewer(goal.id, spec.review_id, ref)
            host.reviewed.append(spec)
            host.children[ref] = ChildStatus(ref, "running")
            raise ConnectionError("ACK lost after actual independent reviewer bound")

        host.start_reviewer = started_but_ack_lost
        with pytest.raises(ConnectionError):
            await service.request_complete(goal.id, paths=("result.txt",))
        saved = await service.status(goal.id)
        assert saved.review.worker.id == "actual-reviewer" and saved.review.state == "running"
        await service.reconcile(goal.id)
        assert len(host.reviewed) == 1


async def test_oversized_real_evidence_is_not_silently_omitted_or_auto_passed(tmp_path):
    from xdog.goal.evidence import MAX_FILE_BYTES

    async with setup_review(tmp_path) as (service, host, goal):
        with (tmp_path / "large.bin").open("wb") as stream:
            stream.truncate(MAX_FILE_BYTES + 1)
        with pytest.raises(ValueError, match="capacity"):
            await service.request_complete(goal.id, paths=("large.bin",))
        assert host.reviewed == [] and (await service.status(goal.id)).status == "active"


async def test_user_pause_is_not_released_by_a_successful_reviewer(tmp_path):
    async with setup_review(tmp_path) as (service, host, goal):
        requested = await service.request_complete(goal.id, paths=("result.txt",))
        await service.pause(goal.id, "User stopped autonomy", context=OperationContext(True))
        finish(host, requested, verdict(requested))
        assert (await service.reconcile(goal.id)).status == "paused"
        await service.resume(goal.id, context=OperationContext(True))
        assert (await service.reconcile(goal.id)).status == "completed"
        assert len(host.reviewed) == 1


async def test_explicit_review_cancel_during_start_stops_exact_late_reference(tmp_path):
    async with setup_review(tmp_path) as (service, host, goal):
        entered, release = asyncio.Event(), asyncio.Event()
        start = host.start_reviewer

        async def delayed(spec):
            entered.set()
            await release.wait()
            return await start(spec)

        host.start_reviewer = delayed
        requesting = asyncio.create_task(service.request_complete(goal.id, paths=("result.txt",)))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await service.cancel_review(goal.id, "Explicit cancel before admission")
            release.set()
            result = await asyncio.wait_for(requesting, 3)
            assert host.cancelled == [result.review.worker]
            assert result.review.state == "cancelled" and result.status != "completed"
        finally:
            release.set()
            await asyncio.gather(requesting, return_exceptions=True)


async def test_revision_does_not_implicitly_cancel_or_lose_an_active_reviewer(tmp_path):
    async with setup_review(tmp_path) as (service, host, goal):
        requested = await service.request_complete(goal.id, paths=("result.txt",))
        await service.revise(
            goal.id, "Changed", ("New original condition",), "User changed criteria", context=OperationContext(True)
        )
        with pytest.raises(ValueError, match="reviewer|review"):
            await service.request_complete(goal.id, paths=("result.txt",))
        assert len(host.reviewed) == 1 and host.cancelled == []
        await service.cancel_review(goal.id, "Explicitly stop old reviewer")
        result = await service.request_complete(goal.id, paths=("result.txt",))
        assert result.review.id != requested.review.id and len(host.reviewed) == 2
