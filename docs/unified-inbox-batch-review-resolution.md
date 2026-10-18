# Unified Inbox/batch independent review and resolution — 2026-10-18

## Provenance and verdict boundaries

This is the **one previously interrupted independent review**, resumed after the
user explicitly reactivated the goal. Reviewer: `Parfit`, agent
`01a1436b-3386-7cf1-87c8-c84c7b07fe0b`. It was read-only, used no implementation
subagents/worktree/model override/provider, and returned its report in the task.
Original base: `8626efc`; reviewed pre-fix base HEAD:
`460e8dfd4b46e3b4ace526f81aa70efdf73c682d` plus the approved uncommitted scheduler
integration. The review package SHA-256 was
`13866385fb1e2ea881e839292d9c90a2eadc0ec6a109e98280a485fb9863ffa7`.

The report covered all original routing/Inbox/batch tasks in the **currently
retained native/SDK/ACP** interfaces, shared refactor imports, and scheduler
integration. Later user-approved frontend RPC/UI/MCP removals were not treated as
missing requirements. It confirmed real Router/Handler separation, one actual
Agent.prompt(list), running/terminal persistence barriers, external-origin/tool
permission separation, source/image/display preservation and source-only timers.

**Reviewer verdict before fixes: Ready = No; Critical0, Important4, Minor0.**
Reviewer personally ran64 focused cases (4.37s), not the whole suite or installed
probes. Four issues were separately reproduced with narrow fault/timing injection.
The author then independently reproduced all4 in permanent failing tests. Runtime
fix commit: **`eae34869ea58daa543a8c205c330931e9c13898f`**. There is **no second reviewer verdict** and no claim that
the reviewer re-reviewed these fixes; the post-fix result is the author's measured
regression/full-suite/artifact acceptance, as required by the selected one-pass
review/fix workflow.

## Important findings and permanent dispositions

All4 retain their Important grading; none was downgraded or dismissed.
Tests are in `packages/coding/tests/test_unified_inbox_review_fixes.py` unless noted.

### I1 — Native shared-batch Escape detached waiters but let preparation continue

- Reviewed locations: native `session_events.py:220–225`, controller cancellation
  `controller.py:614–619` in the pre-fix tree.
- Observed RED: two native members entered one batch, pre-turn compaction blocked,
  Escape ended waiters with execution_continues but did not cancel preparation;
  releasing the barrier still allowed a model call.
- Fix: Native Escape uses the **trusted controller abort**, independent of any
  representative member. It cancels its own unselected/admitting native inputs,
  fences the pump, aborts the actual shared producer and waits for preparation,
  model/tool/finalization cleanup. Remote peer cancel still only detaches a shared
  waiter; no source label grants the global operation.
- Permanent regression:
  `test_native_cancel_shared_batch_settles_preparation_before_any_model_call`:
  actual UI Escape, two selected members plus one queued input, zero model calls,
  preparation cancellation, real terminal receipts and draft restoration.
- **Disposition: fixed; RED→GREEN observed.**

### I2 — Valid 1-MiB receipt failed after internal provenance wrapping

- Reviewed locations: `inbox_runtime.py:252–255` / activation failure path.
- Observed RED: exact1048576-byte InboxMessage was accepted, but adding actual
  request/attempt/branch correlation caused SessionInput capacity rejection and
  broke the owner, leaving pending work behind it.
- Fix: submitted content retains its original **1-MiB and JSON-depth32** budget.
  The canonical correlation wrapper has a separate hard **2048-byte / one-level**
  formatting allowance. Original ID/provenance are measured, and returned data
  still contains the actual request ID, original submitted provenance and host
  message/attempt/branch IDs; no content is truncated or relabeled as human.
  Raw Inbox validation explicitly disables that allowance. Controller and turn
  admission permit it only for a real coordinator-bound, image-free, unmodified
  **singleton**; unbound peers/human inputs and multi-input batches keep the old
  cap. Actual model/context/image limits remain unchanged. This is not authority,
  message approval, a wake credit or a blanket increase to raw ACP frame size.
- Permanent regression:
  `test_maximum_durable_input_survives_host_envelope_and_does_not_poison_following_work`
  executes the exact1-MiB retained message and following normal message, with two
  terminal records/model calls and a healthy owner. Agent codec tests cover maximum
  escaped branch labels, original nesting, unchanged IDs/provenance, over-budget
  content rejection and strict raw/human limits. The unbound-peer test proves
  copying transport-looking fields cannot borrow controller allowance.
- The rebuilt **wheel-only owner probe** also executes the exact1048576-byte input
  and next input, independently of the checkout, confirming two calls and a
  healthy owner; compilation-only verifier tests are not substituted for this run.
- **Disposition: fixed; RED→GREEN observed, installed behavior re-executed.**

### I3 — Partial reservation publication stranded unowned claims

- Reviewed locations: coordinator `inbox_runtime.py:243–248`, store reserve loop.
- Observed RED: a one-shot second-claim write failure left first claimed + second
  pending with no ticket/binding; polling could execute only the second input.
- Fix: reservation publication itself is inside rollback settlement. Only matching
  generation/**attempt ID** unstarted claims from that selection are released;
  attempt history remains and other running attempts are never recovered/reset.
  The coordinator retains completed disk-worker receipts even when the awaiting
  control is canceled. Failed rollback is explicitly published as
  `InboxReservationRollbackError` and fails that owner closed, including when the
  waiting observer is canceled; it never silently declares the drain healthy.
- Permanent regressions cover partial publication + successful automatic retry,
  rollback failure (including canceled observer), preservation of a different
  running record, canceled reservation-observer settlement, and stale cleanup
  unable to reset a new same-generation attempt.
- **Disposition: fixed; original RED→GREEN and additional fault/fence cases passed.**

### I4 — Operator withdrawal during claim→ticket handoff poisoned the owner

- Reviewed locations: `inbox_runtime.py:243–255,347–355` and controller finish path.
- Observed RED: after claim publication but before its receipt/ticket handoff,
  `/inbox cancel` published cancelled. Stale handoff subsequently tried to begin/
  finish that claim and set broken = Inbox result owner/attempt mismatch.
- Fix: an authorized withdrawal fences handoff **before** async cancel IO.
  Admission skips withdrawn members without inventing tickets/history; actual
  disk cancellation still determines the durable receipt. Repeated unstarted
  cancellation is idempotent within the same branch/owner context and reaffirms
  durability without adding attempts. It cannot reset a running/batched record.
  Actual cancellation IO errors remain storage failures, not false successful
  withdrawal acknowledgements.
- Permanent cases place barriers after claim publication, and after cancel
  publication but before the cancel observer returns. They prove no model call,
  no owner break and successful unrelated subsequent work. Repeated pending/
  claimed withdrawal preserves exact attempt facts and rejects a wrong owner.
- The first full fix-pass run also exposed
  `test_acp_batch_intake.py::test_operator_withdrawal_settles_prompt_without_waiting_for_other_turn`:
  operator and prompt cleanup raced on the same unstarted cancellation. The
  idempotent durable operation fixes that reproduced regression; it was not
  skipped, renamed or weakened.
- **Disposition: fixed; RED→GREEN observed, original ACP regression retained.**

## Explicit rulings for every Declined to judge item

| Declined item | Author ruling |
|---|---|
| Registration-level vs per-route collection pause | **Retain the conservative registration-wide pause.** The accepted first-release behavior connects every receiver before collection and stops collection on unavailable/unknown delivery. Handler status/identity/pending receipts/cleanup remain independent; that does not promise per-route high availability or independent source fanout while one destination is uncertain. No unstated partitioning product feature is introduced to force a clean review. |
| Comprehensive new Claw ACP/Inbox correctness | **Outside the original coding/Inbox review objective; not claimed.** The separately approved Claw feature is retained (not rejected using an old non-goal). Whole-root Claw643 cases and actual scheduled ACP→Claw group interop passed. The independent reviewer inspected that integration, not every Claw storage/recovery path; no full Claw review verdict is fabricated. |
| Restoration of removed frontend RPC/UI/MCP surfaces | **Superseded by later explicit user instructions/current AGENTS.** No restoration is appropriate. Native/SDK/ACP and related shared ownership/permission/persistence invariants are the current acceptance target; historical RPC test results are archived, not counted as current proof. |

## Post-fix author verification

All8 configured source paths passed: **6220 passed,34 skipped in76.90s**
(`/tmp/unified-inbox-final-root.log`). Ruff passed; strict mypy passed all8
namespaces (554 files); all17 shipped Flow examples passed; diff whitespace checks
passed. Full-root tests retain actual transport/process/IO/permission coverage.
No new feature cases were skipped. The exact final skip reasons and the disclosed auxiliary output-panel timeout
are recorded in the current acceptance document. A complete serial three-package
audit subsequently passed3801 with34 skips; no flaky-case timeout was relaxed.

Fresh5 wheels/5 sdists from the final runtime were validated by Twine. Four fresh
wheel-only consumers ran outside the source checkout under Python3.13.9 / official
ACP SDK0.12.1, including the SDK six-independent-input one-call case and the new
maximum-input continuation case. Artifact directory:
`/tmp/unified-inbox-reviewed-dist.hR0pRB`; consumer environments:
`/tmp/xdog-acp-isolated.nb7sp8sm`. Every archived Python member and Eyes' skill/two
examples was byte-compared against source; `source-parity.json` records the fix
commit and explicitly includes the uncommitted scheduler working tree.

| Artifact | Python members matched | SHA-256 |
|---|---:|---|
| `xdog_agent-2.4.2-py3-none-any.whl` | 118 | `bbf2ebfba9564d12152a32f3f1a242774b672dd35c159a0dff0cac88310553fc` |
| `xdog_agent-2.4.2.tar.gz` | 327 | `0be27f76bbc8f3742681d0ddf95c9e2e8c675a49b7eea55493935b2c574692b9` |
| `xdog_ai-2.4.2-py3-none-any.whl` | 51 | `8f423ef40763def8800b82ce1232264c89564b7571ce57a530103b49bc6dd061` |
| `xdog_ai-2.4.2.tar.gz` | 93 | `84b4043a1e19ed51dfd7a0657ae86d2931b17fcdf435f35f7624b7a72d6c9c85` |
| `xdog_coding-2.4.2-py3-none-any.whl` | 96 | `e3c96132e364f197e8e2a110d618448aa0798888655a90d7754bbabe424186e2` |
| `xdog_coding-2.4.2.tar.gz` | 228 | `dd45eba463baf74b440624632a0715620d31abb1a584e0072137ddc3b3bb2aa0` |
| `xdog_eyes-2.4.2-py3-none-any.whl` | 31 | `199430045740ce02f239ccbc221c5597ddd08a1a7bb8657dee8b4c33553d2f6d` |
| `xdog_eyes-2.4.2.tar.gz` | 52 | `bb99dcb1b67bdda8fdf0adcc846096263893adbf475c3abc5d14bc795fe51a5d` |
| `xdog_tui-2.4.2-py3-none-any.whl` | 88 | `027a4d74d5e6c47eb322628c3f429631642c2937162566103f08f0547287cf4a` |
| `xdog_tui-2.4.2.tar.gz` | 170 | `891b1f92c15879f4dc4dca5d2068ed22d3fcfd95d20e06c3ed6ac169548936d6` |

## Final acceptance boundary

The current acceptance matrix maps every spec acceptance1–12 and all five review
focus risks to retained source/process/protocol/artifact evidence. The original
review's4 Important findings are closed by author-verified permanent regressions;
its original Ready=No is preserved above rather than rewritten into a second
approval. The final Native task-done command must record a successful whole-root
run before the goal is marked complete.

No real paid provider/IDE, physical power failure, exhaustive croniter/timezone
matrix, TCP/Windows transport, OS sandbox or exactly-once tool-side-effect claim.
Scheduler code remains a separately approved uncommitted feature; no automatic
push/merge/publication or retroactive full-Claw-review claim is part of closing
this original goal.
