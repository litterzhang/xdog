# Coding Goal host adapter — first integration milestone

Date: 2026-10-19. Historical explicit-host API milestone. Automatic opt-in
lifecycle/configuration was subsequently implemented and verified; see
`docs/coding-goal-lifecycle-verification.md` for the current milestone.
The evidence below describes the earlier explicit-API task only.

## Actual integration

`CodingGoalHost.open(session)` installs an explicitly created parent Goal tool
and small GoalHost ports over the **existing** CodingChildren/ChildSupervisor.
Goal continues to own its own leased journal. One-use task-local host admission
binds the role to actual child identity, not a configurable label. Workers and
independent reviewers use fresh actual Agents with existing child tool loops,
model metadata, compaction/persistence and extension hooks.

Bound context/report tools bridge to the service's owning loop. Their narrow
structured capability is checked by actual selected tool-object identity in the
immutable execution context, not a tool-name permission exemption. General
filesystem, parent readonly, extension and Coding approval policies remain in
force. Reviewer profiles contain only parent-allowed local readonly tools plus
bound original review context; they cannot report/accept tasks or submit a parent
verdict. Original context comes from Goal history, not derived files.

Original-ID notices enqueue directly into the existing durable InboxStore with
exact session/branch/workspace checks. Content-checked receipt recovery handles
lost ACK without standard prompt retry or message approval. Tests leave notices
pending with no parent messages: no second controller/Agent loop is constructed.

Independent ChildSupervisor subscriptions survive UI publish replacement, isolate
listener mutation/failure, and only provide hints. Host reconciliation consults
actual child settlement and committed Goal facts. Reopen reconstructs original
execution references from Goal truth and verifies the real persisted ChildStore
catalog. Submitted reports can be explicitly accepted after restart without
replaying the worker; missing/unknown execution remains unaccepted.

## Bugs found through integration

- Concurrent explicit and lifecycle-triggered application of the same independent
  review was not idempotent. A deterministic two-fingerprint barrier test first
  failed, then passed after recognizing only the exact already-committed next
  revision/execution/findings. Changed evidence/criteria still fail closed.
- Full9 exposed `test_failures_never_mark_success[no-channel]` in the prior Claw
  observer output path. Known unavailable original recipients were temporarily
  marked sending and pending report storage blocked terminal source checkpoint
  release. Two deterministic regressions first failed. Generic recipient
  availability preflight + known-not-sent deferral avoid fictitious sends;
  source completion no longer discards or depends on independent report outboxes.
  This is not the final per-assistant-message output integration.

## Verification

- Coding11 + independent subscription2 + Goal63: **76 passed**,8.53s
  (`/data/t.Vy5SaQ/coding-goal-focused-final.log`). Report regression suite13 passed.
- After the named no-channel defect was fixed, full9 **6983 passed,34 skipped**,
  154.18s (`/data/t.Vy5SaQ/coding-goal-verified-root9.log`), including Claw1230
  and Coding1238/12 skipped. Earlier failed root runs are retained in the ledger.
- Strict9 mypy593 files, Ruff packages, Flow17, lock/diff checks passed.

## Scope still open

The explicit adapter requires a trusted caller to own startup/teardown on its
callback loop. Global disabled-by-default flags, optional extras, automatic
owned-session installation, SDK loop retention, navigation retirement, installed
Coding lifecycle verification are the next task. Do not use this intermediate
branch as a completed deployment. Claw still awaits the new Goal adapter and old
GoalManager/Tracker removal. Shared Goal UI/read-only extension views and the
whole47-item acceptance audit/fresh-context review remain. No push/merge/publish,
provider request or user service/data change occurred.
