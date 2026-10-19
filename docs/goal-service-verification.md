# Goal explicit worker/report/notice service — verification

Date: 2026-10-19. Kernel lifecycle milestone, not a claim of real host/UI cutover.

GoalService now uses small asynchronous GoalHost data ports (owner, worker/reviewer
specs, actual execution reference/status/cancel and content-checked idempotent
notice enqueue/receipt). No Agent factory, Session, Runtime, Controller, Eyes,
Claw/Coding or TUI reference/import is accepted in the Goal kernel.

Creation/criteria/user-pause release require a host-produced OperationContext;
source labels or a dictionary cannot grant authority. Work is explicitly assigned,
starting truth precedes the host call, real worker binding is durable, and a lost
start ACK cannot cause a second implicit worker. Unknown unbound start records
interruption and requires explicit inspection/retry. Actual host admission can
bind a worker before it reports; the bind is storage-only so an admitting child
never waits for cancellation of itself.

Worker reporting is fixed to one Goal/task/attempt, revision/content immutable,
and commits original evidence plus a deduplicated notice atomically. Submitted
is not accepted; acceptance checks selected actual digest/revision/attempt and
host worker settlement. A task's accepted state is not Goal completion. Concurrent
reports and early reports during start cannot be lost or overwritten by binding.

User pause cannot be bypassed through wait/resume. Goal wait/pause/abandon and
notification stop never implicitly cancel another resource; cancel_task is the
separate explicit worker action. Earlier cancellation during a pending start is
fulfilled on that exact late reference. Owned operations settle admitted effects
before returning caller cancellation/closing storage. Close does not act as UI-
triggered worker cancellation; actual execution resources remain host-owned.

Notices use the original stable ID through an idempotent enqueue-only host port,
never standard prompt replay or message approval. Unknown ACK can be resolved by
an owner/content-checked receipt; old send ACK cannot settle a newer attempt.
Disable cancels known pending notices atomically but never retracts already
accepted/admitted host input. No active-Goal timer/planner/model task is created.

Evidence: real GoalStore and controlled external-host boundary37 tests passed,
including dependency gates, actual report selection, concurrent writes, start/
report/cancel races, lost ACK, wrong owner and user authority. Full9 root:
**6937 passed,34 skipped**,144.75s, Goal37/Claw1228, log
`/data/t.Vy5SaQ/goal-service-root9.log`. Strict9 mypy584 files and Ruff/diff passed.

Explicit independent review, Goal-local evidence scanner, role tools/skills,
real Claw/Coding adapters and shared Goal extension UI remain. Fake-host tests
are not substituted for the required real host/installed lifecycle evidence.
Legacy Claw Goal still awaits explicit cutover/removal; full47-item goal active.
