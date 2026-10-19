# Standalone Goal storage foundation — verification

Date: 2026-10-19. This is the actual ninth distribution/storage foundation, not
the completed GoalService, host cutover or shared UI feature.

## Implemented

`packages/goal` provides typed immutable domain records and strict bounded exact
JSON codecs. Goal, criteria revisions, task attempts/reports/selected acceptance,
independent review snapshots and notices are separate facts. A submitted report
cannot construct accepted task truth without the selected actual report digest;
a completed Goal requires ordered current evidence-backed independent findings.
Business host/start/evidence checks will additionally live in GoalService.

`GoalStore` owns `xdog.goal.journal.v1`, its private directory/lease and all Goal
snapshots, immutable versions, original evidence artifacts and notification send
attempts. Owner is opaque host key+workspace, never a model argument. State,
history, artifacts and notices commit atomically with optimistic version checks.
Conflicting/stale writes roll back; mutable artifact read results cannot alter
truth. Every disk operation uses owned off-loop settlement.

Recovery records starting/running work/reviews interrupted and sending notices
unknown; it does not construct an Agent, choose a source, start a worker, replay
side effects or wake a model. Unsupported schema/format, corruption, another
owner/workspace or a second writer is refused before recovery writes. No old
Claw Goal file import/migration/deletion or alternate backend exists.

Root workspace/dev installation, ninth pytest path, CI import/type gate and
manual publish inventory know Goal. This enables no Claw/Coding feature or UI;
no publish workflow was run. Direct runtime dependency is only `xdog-agent` and
Python stdlib. There is no Claw/Coding/TUI/Eyes/ACP/Flow import in Goal.

## Evidence

- Goal **19 tests**: real SQLite history/CAS/outbox atomic rollback and artifacts,
  independent owner/lease, strict immutable/DAG/report/version codecs, byte-
  preserving old/corrupt schema/state, actual child-process exit and recovery,
  cancellation while a real admitted disk operation holds its lease, fenced stale
  notification ACK and historical send corruption refusal.
- Focused Goal+publish/package contracts **31 passed**. The earlier root discovered
  the old publish inventory expectation; it was updated for Goal while preserving
  site/umbrella exclusion. No safety test was disabled.
- Final full9 root: **6919 passed,34 skipped**,144.38s;
  `/data/t.Vy5SaQ/goal-store-final-root9.log`. Goal19, Claw1228.
- Strict9 mypy **580 source files**, no issues; Ruff packages and artifact verifier,
  Flow examples **17 passed**, lock/diff checks passed.
- Goal/Agent/AI wheels+sdists at `/data/goaldist.QAIsm5`: Twine, direct dependency/
  py.typed checks and runtime Python source-byte parity passed.
- `scripts/verify_goal_artifact.py` creates an actual **Goal-only fresh wheel**
  environment outside the checkout under
  `/data/t.Vy5SaQ/goal-only-installed-zn171l_q`. No installed/imported host, TUI,
  Eyes, Flow or site. It commits/reopens actual history/artifacts/unknown notice
  facts with **zero model calls**. No user data/service or provider was touched.

## Not yet delivered

Small asynchronous GoalHost ports, explicit worker/report/acceptance operations,
notification transport, independent review/evidence execution, role tools/skills,
Claw/Coding adapters and the same Goal Python UI extension remain implementation
work. Legacy Claw GoalManager/Tracker/turn-end reviewer/native Goal UI still await
actual consumer cutover/deletion. Goal-only storage smoke does not substitute for
full lifecycle or both real installed host adapters. The overall47-item objective
and fresh-context final whole-branch review remain open.
