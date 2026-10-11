# UI-046: input/history admission crash boundary

Current Linux checkpoint: 2026-10-10. This closes the historical child queue
consumption/history and handled-input persistence boundary, not external tool
exactly-once effects, physical power loss (UI-058), or final whole-goal gates.

## Protocol now used by both actual host child factories

1. Queue acceptance/edit retains the existing write-ahead input journal. An accepted
   input is not a consumed/admitted receipt.
2. Before publishing user MessageEnd/identified InputConsumed, persist the prospective
   full typed child history. Before publishing InputHandled, persist bounded explicit
   handler feedback without fabricating history. Initial task admission is covered too.
3. Admission sidecar uses temp write + file fsync + atomic rename + directory fsync.
   Its SHA-256 base-record identity prevents an orphan sidecar from overriding a newer
   terminal record, including a terminal record whose status itself is interrupted.
4. Only after checkpoint success publish logical admission and run the existing
   awaited pending-queue settlement; model continuation waits for both. Failed
   checkpoint does not publish a consumed/handled receipt or enter the next model.
5. A crash between files is explicitly uncertain: committed history/feedback is
   recoverable together with the older original input/image snapshot until queue
   settlement commits. There is no automatic replay or implicit agent restart.
   Callback/tool effects are not rolled back or claimed exactly-once. A callback may
   have effects before its feedback is durable; recovered original input is uncertain.
6. Terminal cleanup writes authoritative final state first, then removes sidecars.
   Write/cancellation failures retain queue/initial uncertainty. Cancellation and
   close remain reachable during storage ownership and wait for actual writer exit;
   ordinary concurrent queue edits/new inputs remain refused while storage owns work.

Owned worker threads are awaited under cancellation; no cancellation timeout is
misrepresented as actual settlement. Existing immutable history, permission hooks,
read-only/default-deny/explicit allow-all, branch/host scope and tool loop are retained.
Permission gates remain application policy, not an OS sandbox.

## Current named evidence

`packages/agent/tests/test_child_admission_checkpoint.py` provides:

- Receipt ordering, checkpoint refusal with no model/false user admission.
- Real owned Linux SIGKILL at before checkpoint, before rename, after rename before
  directory receipt, after checkpoint, before queue settlement and after settlement,
  for transformed input with original image bytes and handled feedback.
- Reload verifies old/new committed history and feedback, original uncertain queue
  bytes before settlement, no consumed queue after settlement, stopped ownership and
  constructor/model replay refusal.
- Held writer cancellation/close before and after replacement, exact writer lifetime,
  visible uncertain initial recovery, no provider entry and no lingering sidecar.
- Replace/directory-fsync failure and orphan cleanup, including completed/canceled/
  interrupted terminal precedence, plus corrupt/foreign saved-record refusal.

Actual Coding and Claw host-factory test in `test_child_image_admission.py` proves
production `persist_admission` wiring, image-bearing initial history durable while
provider entry is still blocked, parent unchanged, and cancel waiting for storage.
Existing Claw socket reader ping/wait/cancel, child approvals/access, queue/recovery,
images, canonical RPC undrained output and owner/lifecycle tests remain in the gate.

Final current-source integrated command covers thirteen files:

```sh
uv run pytest \
 packages/agent/tests/test_child_admission_checkpoint.py \
 packages/agent/tests/test_child_input_journal.py \
 packages/agent/tests/test_supervision_store.py \
 packages/agent/tests/test_identified_inputs.py \
 packages/agent/tests/test_input_handled.py \
 packages/agent/tests/test_input_transform.py \
 packages/coding/tests/test_child_agents.py \
 packages/claw/tests/test_child_agents.py \
 packages/coding/tests/test_child_image_admission.py \
 packages/coding/tests/test_child_image_submission.py \
 packages/coding/tests/test_child_queue_ui.py \
 packages/coding/tests/test_rpc_child_canonical.py \
 packages/coding/tests/test_rpc_children_cli.py -q --tb=short -rs
```

**229 passed, no skips, 70.23s**, `/tmp/ui046-current-source-final.log`, handle60720
exit0. Root Ruff and strict seven-namespace mypy **487 source files clean**,
`/tmp/ui046-complete-static.log`, handle81583 exit0. Current agent/coding/claw
wheel and sdist built `/tmp/ui046-wheels`, `/tmp/ui046-current-artifacts.log`,
handle58939 exit0; all five production modules byte-compared against both artifact
forms. Named source SHA-256 receipt: `/tmp/ui046-current-source.json`.

## Red evidence and root cause (not erased or counted as passing)

Initial checkpoint tests had overbroad assertions applying the first-event empty
history expectation to subsequent checkpoints and mistook typed content arrays for
plain strings. `/tmp/ui046-checkpoint-current.log`:6 failed/12 passed; corrected
fixture expectations, preserved production contract.

Initial integrated host run:1 failed/176 passed, `/tmp/ui046-current-hosts.log`.
Claw socket cancel race reproduced in whole file and diagnostic reruns, though
isolated case passed. Temporary offline diagnostics proved the exact error:
`Child recovery storage still owns work; wait before changing it`. Existing control
guard refused cancel during new initial admission writes, so the wait request had
nothing to settle. Fix removes only cancel/close from that early refusal, retaining
existing cleanup/writer wait and queue-mutation guards. Dedicated held-writer tests
now cover it deterministically; original ping/wait/cancel test is unchanged, no
longer timeout inflation or skipped/deleted assertion. Final integrated gate green.

UI-046 named acceptance is complete. UI-047 draft windows, UI-055 frozen full-root,
UI-064 all-host final safety and all remaining IDs remain required. The four
non-current-Linux native-validation IDs stay deferred, not claimed passed.
