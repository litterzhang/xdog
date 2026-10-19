"""Explicit independent reviewer execution and evidence-backed completion."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, replace
from typing import TYPE_CHECKING

from xdog.goal.codec import json_text
from xdog.goal.evidence import fingerprint
from xdog.goal.host import ReviewerSpec
from xdog.goal.models import Finding, Goal, Review, WorkerRef, text

if TYPE_CHECKING:
    from xdog.goal.service import GoalService


def findings(value: object, criteria: tuple[str, ...]) -> tuple[str, tuple[Finding, ...], str]:
    if not isinstance(value, dict) or set(value) != {"verdict", "conditions", "summary"}:
        raise ValueError("Independent review requires structured ordered per-condition evidence")
    verdict, rows, summary = value["verdict"], value["conditions"], value["summary"]
    if verdict not in {"passed", "continue", "blocked"} or not isinstance(rows, list) or len(rows) != len(criteria):
        raise ValueError("Review must cover every original condition")
    text(summary, maximum=64 * 1024)
    result = []
    for expected, row in zip(criteria, rows, strict=True):
        if (
            not isinstance(row, dict)
            or set(row) != {"condition", "status", "evidence", "reason"}
            or row["condition"] != expected
            or not isinstance(row["evidence"], list)
        ):
            raise ValueError("Review conditions must match the original ordered criteria")
        result.append(Finding(row["condition"], row["status"], tuple(row["evidence"]), row["reason"]))
    if verdict == "passed" and any(row.status != "passed" for row in result):
        raise ValueError("Every condition must pass before an independent passed verdict")
    json_text(value, maximum=64 * 1024)
    return verdict, tuple(result), summary


class ReviewLifecycle:
    def __init__(self, service: GoalService) -> None:
        self.service = service

    def _excluded(self) -> tuple[str, ...]:
        owner, storage = self.service.store.owner, self.service.store.path.parent
        try:
            return (storage.relative_to(owner.workspace).as_posix(),)
        except ValueError:
            return ()

    async def request(self, identifier: str, paths: tuple[str, ...], summary: str) -> Goal:
        s = self.service
        async with s._lock(identifier):
            goal = await s._get(identifier, live=True)
            if goal.status == "paused":
                raise ValueError("User-paused Goal cannot request autonomous review")
            if goal.review is not None and goal.review.state in {"starting", "running"}:
                if goal.review.paths != paths:
                    raise ValueError("Another explicit review is already active; inspect/cancel first")
                return goal
            if goal.review is not None and goal.review.worker is not None:
                previous = await s.host.child_status(goal.review.worker)
                if previous.worker != goal.review.worker or previous.state in {"running", "settling"}:
                    raise ValueError("Previous reviewer still running/settling; explicitly cancel or wait")
            if any(task.state not in {"accepted", "cancelled"} for task in goal.tasks):
                raise ValueError("Task reports must be explicitly accepted or tasks cancelled before completion review")
            for task in goal.tasks:
                if task.attempts and task.attempts[-1].worker is not None:
                    child = await s.host.child_status(task.attempts[-1].worker)
                    if child.worker != task.attempts[-1].worker or child.state in {"running", "settling"}:
                        raise ValueError("This Goal still has an actual running/settling worker")
        digest = await fingerprint(goal, s.store.owner.workspace, paths, excluded=self._excluded())
        async with s._lock(identifier):
            current = await s._get(identifier, live=True)
            if current.version != goal.version:
                raise ValueError("Goal changed during evidence snapshot; explicitly review current facts")
            review = Review(
                "r-" + uuid.uuid4().hex,
                criteria_revision=goal.criteria_revision,
                fingerprint=digest,
                paths=paths,
                summary=summary,
            )
            await s._save(
                goal,
                review=review,
                status="reviewing",
                artifacts=((f"review-context:{review.id}", {"version": goal.version + 1}),),
            )
            prompt = (
                f"Independently verify Goal {goal.id}, review {review.id}. "
                "Use your bound goal_context tool for the original full contract/criteria and selected evidence. "
                "Inspect actual evidence using allowed read-only tools. Do not edit work, criteria or accept tasks. "
                "Return structured JSON with verdict passed|continue|blocked, summary, and conditions: "
                "[{condition:<exact original>,status:passed|failed|blocked,evidence:[...],reason:...}]. "
                "Script exit codes or parent claims alone are not a verdict."
            )
            spec = ReviewerSpec(goal.id, review.id, goal.criteria_revision, prompt)
        try:
            reference = await s.host.start_reviewer(spec)
            s._owner()
            bound = await self.bind(identifier, review.id, reference)
            if bound.review is not None and bound.review.state == "cancelled":
                await s.host.cancel_child(reference)
            return bound
        except BaseException:
            async with s._lock(identifier):
                current = await s._get(identifier)
                if current.review is not None and current.review.id == review.id and current.review.worker is None:
                    await s._save(
                        current,
                        review=replace(current.review, state="interrupted"),
                        status="waiting",
                        wait_reason="Reviewer start outcome unknown; inspect before explicit request_complete",
                    )
            raise

    async def bind(self, identifier: str, review_id: str, reference: WorkerRef) -> Goal:
        s = self.service
        if not isinstance(reference, WorkerRef):
            raise ValueError("Reviewer must be an actual independent host execution")
        async with s._lock(identifier):
            goal = await s._get(identifier)
            review = goal.review
            if review is None or review.id != review_id:
                raise ValueError("Original review changed before actual admission")
            if any(attempt.worker == reference for task in goal.tasks for attempt in task.attempts):
                raise ValueError("A task worker cannot become its own independent reviewer")
            if review.worker is not None:
                if review.worker != reference:
                    raise ValueError("Reviewer execution changed")
                return goal
            bound = replace(review, worker=reference, state="running" if review.state == "starting" else review.state)
            return await s._save(goal, review=bound)

    async def cancel(self, identifier: str, reason: str) -> Goal:
        s = self.service
        text(reason)
        async with s._lock(identifier):
            goal = await s._get(identifier)
            review = goal.review
            if review is None:
                return goal
            saved = await s._save(
                goal,
                review=replace(review, state="cancelled"),
                status="waiting" if goal.status == "reviewing" else goal.status,
                wait_reason=reason,
            )
        if review.worker is not None:
            await s.host.cancel_child(review.worker)
        return saved

    async def reconcile(self, identifier: str) -> Goal:
        s = self.service
        goal = await s._get(identifier)
        review = goal.review
        if review is None or review.state != "running" or review.worker is None:
            return goal
        child = await s.host.child_status(review.worker)
        if child.worker != review.worker:
            raise ValueError("Review result belongs to another actual execution")
        if child.state in {"running", "settling"}:
            return goal
        raw: object = None
        try:
            if child.state != "succeeded":
                raise ValueError("Independent reviewer did not succeed")
            raw = json.loads(child.result_json)
            verdict, rows, summary = findings(raw, goal.criteria)
            digest = await fingerprint(goal, s.store.owner.workspace, review.paths, excluded=self._excluded())
            if digest != review.fingerprint or review.criteria_revision != goal.criteria_revision:
                raise ValueError("Actual evidence or original criteria changed during review")
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            async with s._lock(identifier):
                current = await s._get(identifier)
                if current.review != review:
                    return current
                return await s._save(
                    current,
                    review=replace(review, state="invalid"),
                    status="waiting" if current.status == "reviewing" else current.status,
                    wait_reason=str(exc),
                    artifacts=(
                        (
                            f"review-result:{review.id}",
                            {
                                "worker": asdict(child.worker),
                                "status": child.state,
                                "result_json": child.result_json,
                                "error": child.error,
                            },
                        ),
                    ),
                    notices=s._notice(
                        current, "review:" + review.id, "Independent review needs inspection: " + str(exc)
                    ),
                )
        async with s._lock(identifier):
            current = await s._get(identifier)
            if current.version != goal.version or current.review != review:
                raise ValueError("Goal changed before applying independent review")
            if current.status == "paused":
                # A user pause is not released by a background review result.
                return current
            result = replace(
                review,
                state="passed" if verdict == "passed" else verdict,
                findings=rows,
                verdict=verdict,
                summary=summary,
            )
            return await s._save(
                current,
                review=result,
                status="completed" if verdict == "passed" else "blocked" if verdict == "blocked" else "waiting",
                summary=summary,
                wait_reason="" if verdict == "passed" else summary,
                artifacts=((f"review-result:{review.id}", {"worker": asdict(child.worker), "result": raw}),),
                notices=s._notice(current, "review:" + review.id, f"Independent review verdict: {verdict}. {summary}"),
            )
