# Assistant-core independent-review fixes

Date: 2026-10-20. This records the single TDD fix pass for the five accepted
Important findings in `claw-assistant-core-independent-review.md`. No second
reviewer was dispatched. Original minor findings remain deferred.

## I-1 / I-5: terminal result and callback lifetime

Commit `1e243af`. `test_review_terminal_receipts.py` reproduced successful
>1MiB ASCII/UTF8 output poisoning the input owner after complete transcript
persistence, and detached queued withdrawal retaining callback/attachment graphs.
Actual RED: 3 failures/2.50s. InputService now projects aggregate response/error
into its existing combined 1MiB receipt bound, explicitly marking previews,
original byte counts and durable session/effects references; full conversation
history is unchanged. Genuine persistence failure still fences execution.

Core terminal advisories cover normal settlement, withdrawal and interruption,
not released pending claims. Orchestrator retires only the exact original
cancelled call/envelope, completes its local display future and never releases
another input's producer/controller. Actual two-peer Native queued abort4x,
detached callback256KiB graph GC, successor/no replay and projection cases pass.
Focused receipt/native/store/runtime: 65 passed/9.97s; strict9/Ruff passed.

## I-2: abandoned outcome remains authoritative

Commit `fc0c23f`. `test_review_abandoned.py` uses real SDK Agent/Supervisor and
holds a reviewer after an actual file read, abandons the Goal, then releases the
reviewer. Passed/continue/blocked were RED (3 failures; failed outcome already
preserved status). The final locked application now keeps completed/abandoned
status, summary and reason while recording independent late review facts.
It does not implicitly cancel a reviewer. Fingerprint-read/abandon race rejects
stale application; explicit reconciliation can retain facts without resurrection.
Goal review/service plus both actual adapters: 60 passed/11.33s; strict9/Ruff pass.

## I-3: actual channel readiness recovers pending output

`test_review_readiness.py::test_gateway_restart_recovers_original_weixin_output_after_channel_ready_without_eyes`
starts the real Gateway, creates a positively not-sent durable Weixin output,
stops it and starts a new real Gateway with Eyes disabled and a model that must
never run. Before the fix, no output was delivered. Readiness now announces an
application-owned, coalesced asynchronous output recovery job AFTER successful
channel connection, including channels registered after application startup.
Actual send lanes, original recipient/context/output ID and unknown-state fences
are unchanged. Startup does not await entered network sender work.

The wheel-only `verify_claw_output_artifact.py` now exercises real Gateway
startup; its former manual `outputs.resume()` masking the defect is removed.
Only local scripted API/model boundaries are substituted, including blocking
local get_updates so the fixture never polls the real network. Repeated readiness
hints do not resend a delivered original output.

## I-4: released Inbox capacity retries original Goal notices

`test_review_readiness.py::test_actual_child_notices_retry_when_parent_input_releases_capacity`
uses a real cap1 Inbox and an active held human turn, while a genuine supervised
Goal worker reads context/file and submits its report. Notices become unknown
under real backpressure. Before the fix, releasing the human turn left them
stranded (RED). GoalHost now subscribes to the generic Core terminal hint only
after service initialization, filters its exact scope, coalesces drain jobs and
unsubscribes on close. InputService exposes read-only intake lifecycle state, so
shutdown settlement does not initiate new Goal handoffs. No Goal schema/policy
was added to Core/ACP/Controller, and no capacity or model polling was introduced.

The existing Goal outbox checks original receipts first, then same-ID idempotent
enqueue; failed receipt reads never authorize replay. No quota was relaxed.
Actual cancellation-of-notifications and application-shutdown cases remain
non-delivery. Stable notice IDs each have one actual consumed execution attempt.

## Observed validation

- I-3/I-4 RED: 2 failed, 2 passed/11.15s; GREEN: 4 passed/4.55s.
- Related Gateway/input/output/Weixin/Goal suite: 99 passed/22.34s.
- Strict9: 603 source files; Ruff packages/scripts, Flow17, lock/diff pass.
- Final unfiltered9 with pinned independent MCP SDK: **7077 passed, 7 skipped**,
  225.80s. The remaining seven are explicit real Codex/Claude CLI opt-ins; no
  credentials/provider were used. All 27 optional SDK cases actually ran.
- Fresh20 artifacts built; Twine20 and byte parity644 source/resources plus29
  explicit Flow examples pass. Four actual isolated cold consumers and the
  installed20+20 cumulative-page probe pass. Exact receipts/hashes are recorded
  in `claw-assistant-core-final-artifacts.json`; earlier candidate artifacts are
  historical, not final proof.

## Deferred minors / non-goals

Do not silently fix the old README reconnect/draft-resubmit wording or the two
cosmetic EOF blanks in observers/models.py and Goal LICENSE in this runtime pass.
Paid/real-account, hardware power-loss certification, non-Linux/TCP, migration
and cross-host takeover remain the original explicitly excluded boundaries.
