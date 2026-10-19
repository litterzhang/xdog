"""Goal worker/report transitions; actual execution remains a host port."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

from xdog.goal.host import WorkerSpec
from xdog.goal.models import Acceptance, Attempt, Goal, Report, WorkerRef, report_digest, state, text

if TYPE_CHECKING:
    from xdog.goal.service import GoalService


@dataclass(frozen=True)
class BoundReporter:
    _service: GoalService
    goal_id: str
    task_id: str
    attempt_id: str

    async def report(self, value: Report) -> Goal:
        if not isinstance(value, Report) or (value.task_id, value.attempt_id) != (self.task_id, self.attempt_id):
            raise ValueError("Report does not match its bound worker scope")
        return await self._service.report(self.goal_id, value)


class WorkerLifecycle:
    def __init__(self, service: GoalService) -> None:
        self.service = service

    async def assign(self, identifier: str, task_id: str, assignment: str, access: str) -> Goal:
        s = self.service
        text(assignment)
        state(access, {"read-only", "inherit"})
        async with s._lock(identifier):
            goal = await s._get(identifier, live=True)
            if goal.status in {"paused", "reviewing"}:
                raise ValueError("Paused/reviewing Goal cannot assign work")
            task = s._task(goal, task_id)
            if task.state in {"starting", "running", "accepted"}:
                raise ValueError("Task already active/accepted; do not start another worker")
            if any(s._task(goal, dep).state != "accepted" for dep in task.depends_on):
                raise ValueError("Task dependencies have not been accepted")
            if task.attempts and task.attempts[-1].worker is not None:
                outcome = await s.host.child_status(task.attempts[-1].worker)
                if outcome.worker != task.attempts[-1].worker or outcome.state in {"running", "settling"}:
                    raise ValueError("Previous actual worker is still running/settling")
            attempt = Attempt("a-" + uuid.uuid4().hex, assignment=assignment, access=access)
            task = replace(task, state="starting", attempts=(*task.attempts, attempt), acceptance=None)
            goal = await s._save(
                goal,
                tasks=s._replace_task(goal, task),
                status="active",
                wait_reason="",
                artifacts=((f"assignment-context:{attempt.id}", {"version": goal.version + 1}),),
            )
            prompt = (
                f"Goal {goal.id}; task {task.id}; attempt {attempt.id}. "
                "The host supplies your bound original goal_context (contract, criteria and full assignment). "
                "Use task_report for this attempt only; it cannot accept a task or complete a Goal. "
                f"Task preview: {task.description[:1024]}\nAssignment preview: {assignment[:2048]}"
            )
            spec = WorkerSpec(goal.id, task.id, attempt.id, prompt, access)
        try:
            reference = await s.host.start_worker(spec)
            s._owner()
            if not isinstance(reference, WorkerRef):
                raise ValueError("Host did not return an actual worker execution reference")
        except BaseException:
            async with s._lock(identifier):
                current = await s._get(identifier)
                selected = s._task(current, task_id)
                if (
                    selected.attempts[-1].id == attempt.id
                    and selected.state in {"starting", "running"}
                    and selected.attempts[-1].worker is None
                ):
                    ended = replace(selected.attempts[-1], state="interrupted")
                    selected = replace(selected, state="interrupted", attempts=(*selected.attempts[:-1], ended))
                    await s._save(
                        current,
                        tasks=s._replace_task(current, selected),
                        wait_reason="Worker start outcome unknown; inspect host effects before explicit retry",
                    )
            raise
        saved = await self.bind(identifier, task_id, attempt.id, reference)
        selected = s._task(saved, task_id)
        if selected.state == "cancelled":
            await s.host.cancel_child(reference)  # Fulfil earlier explicit cancellation after late admission.
        return saved

    async def bind(self, identifier: str, task_id: str, attempt_id: str, reference: WorkerRef) -> Goal:
        if not isinstance(reference, WorkerRef):
            raise ValueError("Expected the actual host worker reference")
        s = self.service
        async with s._lock(identifier):
            current = await s._get(identifier)
            selected = s._task(current, task_id)
            if not selected.attempts or selected.attempts[-1].id != attempt_id:
                raise ValueError("Goal task attempt changed during host admission")
            latest = selected.attempts[-1]
            if latest.worker is not None:
                if latest.worker != reference:
                    raise ValueError("Goal worker execution changed during admission")
                return current
            bound = replace(latest, worker=reference, state="running" if latest.state == "starting" else latest.state)
            selected = replace(
                selected,
                state="running" if selected.state == "starting" else selected.state,
                attempts=(*selected.attempts[:-1], bound),
            )
            return await s._save(current, tasks=s._replace_task(current, selected))

    async def report(self, identifier: str, value: Report) -> Goal:
        if not isinstance(value, Report):
            raise ValueError("Expected a typed bound worker report")
        s = self.service
        async with s._lock(identifier):
            goal = await s._get(identifier)
            task = s._task(goal, value.task_id)
            old_attempt = next((item for item in task.attempts if item.id == value.attempt_id), None)
            if old_attempt is None:
                raise ValueError("Unknown/stale report attempt")
            previous = next((item for item in old_attempt.reports if item.revision == value.revision), None)
            if previous is not None:
                if previous == value:
                    return goal  # ACK loss does not turn the same immutable report into new work.
                raise ValueError("Report revision/content conflict")
            if (
                goal.status in {"completed", "abandoned"}
                or not task.attempts
                or task.attempts[-1].id != value.attempt_id
                or task.state in {"accepted", "cancelled"}
            ):
                raise ValueError("Report belongs to an inactive or stale worker scope")
            latest = task.attempts[-1]
            if latest.reports and value.revision <= latest.reports[-1].revision:
                raise ValueError("Report revision must increase")
            if value.state == "submitted" and not value.evidence:
                raise ValueError("Submitted report requires actual evidence")
            if value.state == "blocked" and not value.blockers:
                raise ValueError("Blocked report requires a reason/blocker")
            report_state = {"submitted": "reported", "blocked": "blocked", "running": latest.state}[value.state]
            updated = replace(latest, state=report_state, reports=(*latest.reports, value))
            task_state = {"submitted": "submitted", "blocked": "blocked", "running": task.state}[value.state]
            task = replace(task, state=task_state, attempts=(*task.attempts[:-1], updated))
            notices = s._notice(
                goal,
                f"report:{task.id}:{latest.id}:{value.revision}",
                f"Task {task.id} report revision {value.revision} is {value.state}: {value.summary}",
            )
            return await s._save(
                goal,
                tasks=s._replace_task(goal, task),
                notices=notices,
                artifacts=((f"report:{task.id}:{latest.id}:{value.revision}", asdict(value)),),
            )

    async def accept(
        self, identifier: str, task_id: str, attempt: str, revision: int, digest: str, summary: str
    ) -> Goal:
        s = self.service
        text(summary)
        async with s._lock(identifier):
            goal = await s._get(identifier, live=True)
            task = s._task(goal, task_id)
            if (
                task.state != "submitted"
                or not task.attempts
                or task.attempts[-1].id != attempt
                or not task.attempts[-1].reports
            ):
                raise ValueError("Task report/attempt changed before acceptance")
            latest = task.attempts[-1]
            report = latest.reports[-1]
            if report.state != "submitted" or report.revision != revision or report_digest(report) != digest:
                raise ValueError("Selected report revision/digest changed")
            if latest.worker is None:
                raise ValueError("Actual worker reference unavailable; inspect start outcome")
            outcome = await s.host.child_status(latest.worker)
            if outcome.worker != latest.worker or outcome.state != "succeeded":
                raise ValueError("Actual worker is running/settling or did not succeed")
            s._owner()
            accepted = replace(task, state="accepted", acceptance=Acceptance(attempt, revision, digest, summary))
            return await s._save(goal, tasks=s._replace_task(goal, accepted))

    async def cancel(self, identifier: str, task_id: str, reason: str) -> Goal:
        s = self.service
        text(reason)
        async with s._lock(identifier):
            goal = await s._get(identifier)
            task = s._task(goal, task_id)
            reference = task.attempts[-1].worker if task.attempts else None
            if task.state == "accepted":
                # Cancellation concerns resources; never invalidate an accepted report snapshot.
                saved = goal
            else:
                attempts = (*task.attempts[:-1], replace(task.attempts[-1], state="cancelled")) if task.attempts else ()
                task = replace(task, state="cancelled", attempts=attempts)
                saved = await s._save(goal, tasks=s._replace_task(goal, task), wait_reason=reason)
        if reference is not None:
            await s.host.cancel_child(reference)
        return saved

    async def reconcile(self, identifier: str) -> Goal:
        s = self.service
        initial = await s._get(identifier)
        for task in initial.tasks:
            if not task.attempts or task.attempts[-1].worker is None:
                continue
            attempt = task.attempts[-1]
            reference = attempt.worker
            assert reference is not None
            outcome = await s.host.child_status(reference)
            if outcome.worker != reference:
                raise ValueError("Host returned another child execution")
            if outcome.state in {"running", "settling"}:
                continue
            key = f"child:{attempt.id}:{outcome.state}"
            async with s._lock(identifier):
                goal = await s._get(identifier)
                selected = s._task(goal, task.id)
                if selected.attempts[-1].id != attempt.id or await s.store.artifact(identifier, key) is not None:
                    continue
                if selected.state in {"starting", "running"}:
                    updated = replace(selected.attempts[-1], state="interrupted")
                    selected = replace(selected, state="interrupted", attempts=(*selected.attempts[:-1], updated))
                notices = s._notice(
                    goal,
                    key,
                    f"Task {task.id} worker is {outcome.state}. "
                    "Inspect its report/evidence; worker completion is not task acceptance.",
                )
                await s._save(
                    goal,
                    tasks=s._replace_task(goal, selected),
                    notices=notices,
                    artifacts=(
                        (
                            key,
                            {
                                "worker": asdict(reference),
                                "state": outcome.state,
                                "result_json": outcome.result_json,
                                "error": outcome.error,
                            },
                        ),
                    ),
                )
        return await s._get(identifier)
