# Observer-owned journal checkpoint — 2026-10-19

This is the storage prerequisite for atomic ACP/managed-observer cutover, **not**
a claim that the running adapter has already been replaced or the47-item goal is
complete. The active manager still uses its old bridge until Task3; no fallback,
dual decoder, schema upgrade or compatibility branch was added to the new journal.

## Data/ownership boundaries implemented

- ObserverJournal owns its private versioned SQLite file/lease, definitions,
  cursor+whole immutable outbox, transport state and notification/report outcomes.
- Its ReceiptReader uses only public core namespace/get/attempts APIs. No raw SQL
  join, mutation or cancellation of core inputs belongs to this journal.
- Cancel collection, cancel notifications and explicitly withdraw core input are
  different operations. Late ACK after transport admission preserves the accepted
  work/report; never-sent cancelled outbox cannot blacklist an external message ID.
- Error updates are revision-fenced and cannot reenable an explicitly stopped
  observer. Unknown channel sends are needs-review, not automatic resends.
- An occurrence binds its original actual execution attempt. A later explicit
  input retry cannot steal that report or mark it delivered. Known-not-sent report
  recovery reconstructs the original immutable attempt, without another model call.
- Core schema is now `xdog.claw.inbox.v3`: InputAttempt.started preserves real
  admission even when both preparation and execution end as failed with identical
  error text. No error-string inference or previous-format upgrade is used.
- Frozen observer data moved into a model-only module. The new journal does not
  import ACP or the old journal implementation.

Agent's small LeasedSqlite utility owns only private/no-follow filesystem and IO
settlement. The observer/Goal domain owns schema/validation/recovery. It creates a
new schema in a private temporary database and atomically installs it exclusively;
failed schema creation cannot poison the final path or overwrite existing data.
Validation is read-only before recovery; cancellation joins admitted transactions
before lease release and reports real disk error precedence. Future standalone
Goal can reuse this helper without depending on Claw storage.

## Evidence

- Journal initial RED12; transport-cancel/binding-corruption RED3; late-error and
  cross-attempt report fences RED2 reproduced before permanent fixes.
- Admission-fact RED2 reproduced the missing explicit started fact.
- Additional leased-IO tests are characterization/fault evidence, not fabricated
  pre-implementation failures. Real rollback/commit, lease exclusion, invalid
  permissions/symlinks, read-only validation and unknown versions are exercised.
-53 focused leased-IO/journal/scope/old-store tests passed4.55s.
- Final root: **6800 passed,34 skipped**, Claw1128, all8 paths106.51s; no new skips.
- Ruff packages and strict8 namespace mypy570 source files passed; lock/diff checks
  and17 Flow examples passed.
- Agent/Claw fresh wheels/sdists `/tmp/claw-observer-journal-dist.NUiijl`: helper,
  journal/models and core-store members byte-match checkout. This is packaging
  presence/parity, not Goal-only or cross-host fresh-install acceptance.

The first balanced run exposed a pending consumer teardown warning despite green
assertions. A temporary diagnostic-only weak-owner probe identified two existing
progress-delivery test cases that never stopped their Orchestrator. They now close
it in finally without changing message/order/error assertions. The initial full
serial probe passed1128 and recorded those exact owners; the fixed focused probe
passed10 with an empty unstopped-owner log. The final balanced root above contains
no pending-task/unretrieved-exception warning. Production lifecycle was not changed
to hide the warning. Probe/evidence logs stay in this plan's scratch workspace.

## Required next

Task3 must wire this journal into actual ManagedObservers, make ACP use only core
receipts, remove `acp/inbox.py` and its consumer/report/Goal hooks, and update real
SDK/process/cancel tests to the separate new contracts. Source and report controls
must remain explicit Agent decisions. Temporary old Goal hint projection must live
in its capability, then disappear with reusable Goal extraction. Final native/
Weixin handoff, captured output routing, optional Goal/both host adapters/shared UI,
nine-package artifacts and independent whole-refactor review remain required.
