# Assistant-core independent wholebranch review

Review completed: 2026-10-20. Reviewer Kierkegaard, native agent
01a15688-a10f-71c2-aca8-4c0c018dd1c1, read-only whole66-commit range
33df9083c95e4f838e0eaf34532d3cd3ea6deee0..ef3e0db5b59abd619724b3be7040b46e13c01d49.
Previous ENOSPC attempts created no reviewer session. This was the ONE actual
fresh reviewer; it is now closed. No second review will substitute for TDD fixes.

## Verdict and strengths

Not ready to merge: five Important findings, no Critical findings. Good separation
of durable intake/attempts/history/output, real original-execution controls,
explicit Goal acceptance/independent review, readonly owner-bound UI, display-only
non-veto vs required guard fail-closed, actual process/SDK/wheel evidence.

## Important findings — accepted into one fix pass

- **I-1** `core/inputs/store.py:363-364`, `consumer.py:204-205,278-280`:
  >1MiB aggregate response/error causes finish validation to fault the whole scope
  after Agent/history success; actual receipt remains running, successors rejected.
  Fix truthful bounded receipt projection/reference, preserving full durable
  transcript and genuine storage-failure fencing. Prove successor/no replay.
- **I-2** `goal/review.py:208-222`: late successful reviewer overwrites abandoned
  Goal with completed/blocked/waiting. Keep abandonment authoritative under final
  lock; do not implicitly cancel reviewer. Retain facts/evidence independently.
- **I-3** `orchestrator.py:240-244`, `gateway.py:207-227`: recovery runs before
  Weixin registration/readiness; Eyes-disabled gateway never wakes pending output
  again. Actual gateway start leaves original pending reply stranded. Installed
  output verifier manually resumes and masks wiring. Recover on channel readiness,
  same ID/recipient/context once, no model/new input/manual recovery.
- **I-4** `goal_adapter.py:407-423`, `goal/notifications.py:50-62`: backpressured
  Goal notices remain unknown after Inbox capacity frees; no future Goal event
  triggers retry. Add coalesced owner-bound capacity event, reconcile original-ID
  receipt before idempotent enqueue, no model polling/prompt replay or relaxed cap.
- **I-5** `orchestrator.py:455-458`: cancelled/interrupted return misses exact
  _input_calls retirement; queued withdrawal never executes its usual finally.
  Callback/envelope/images retained indefinitely. Retire matching original call
  on authoritative terminal withdrawal, including detached waiters; do not remove
  successor/other attempt binding or release active execution.

Main re-grade: all five are Important by actual user impact, not because a spec
line happens to name the scenario. Source mechanisms were checked before fixes.
Each enters one TDD fix pass; no passing acceptance claim until actual regressions
and fresh complete gates plus rebuilt installed consumers are green.

## Deferred minors (not part of fix pass)

- README reconnect paragraph54-57 describes old draft restoration/resubmission
  rather than original-ID receipt reconciliation. Native UI itself preserves ID
  and warns before resubmission, so this is documentation accuracy, not a missing
  execution fence; retained as Minor, not silently fixed.
- Full-range diff whitespace: extra blank EOF in observers/models.py and
  goal/LICENSE. Cosmetic; preserve deferred status rather than change license
  or mix unrelated formatting into runtime fix pass.

## Independent verification actually performed

64 native/controller/identified/review focus passed;1382 Claw+Goal+selected Agent/
Coding cases passed116.74s; Ruff and strict602 passed. Four fresh offline wheel
consumers, installed20+20 cumulative pages,20 artifact hashes, exact643 runtime/
resource member parity across9 packages, Twine20 passed(existing Site description
warnings). Logs `/data/t.Vy5SaQ/review-ef3e0db-*`. Reviewer did NOT rerun full7035
root, Flow or lock; their existing main receipts are not fresh reviewer results.
Actual counterexamples used real stores/controllers/Agents/supervisors/gateway
and scripted model/network boundaries, not fake business implementations.

## Declined to judge — main rulings

- Live paid provider / real-account delivery: intentionally prohibited; retain
  deterministic model/network boundaries, no claims of account/provider operation.
  Cost: no production-account conformance certification.
- Hardware power-loss/device guarantees: explicit non-goal; fsync/process crashes
  prove implementation ordering/recovery, not hardware exactly-once. Cost: no
  device/power-loss certification.
- Non-Linux/TCP transports, migration and cross-host Goal takeover: explicit
  non-goals. Supported exact rejection/ownership boundaries remain tested. Cost:
  those capabilities are unavailable, not silently claimed supported.

No reviewer Source/index/HEAD/worktree/service/provider/user-data modifications.
Final source/artifact/47 acceptance remains open pending I-1..I-5 settlement.

## 2026-10-20 implementation resolution (not a second review)

All five accepted Important findings were reproduced and corrected in the one
TDD pass, commits1e243af/fc0c23f/67120ee. Final full9:7077 passed/7 skipped;
strict603/Ruff/Flow17/lock and fresh20-artifact/isolated-consumer gates pass.
See `claw-assistant-core-review-fixes.md` and final artifact manifest. Original
review verdict above is retained as an authentic historical result. No new
reviewer approval is claimed. Deferred minors and declined-scope rulings stand.
