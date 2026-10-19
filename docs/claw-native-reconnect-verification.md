# Native input receipt reconciliation

Date: 2026-10-19. Partial assistant-core milestone, not whole-refactor completion.

## Actual implemented path

The application `NativeInputReceipts` reads the authoritative Core Inbox for one
native ID in its exact persisted group/workspace namespace. The private Gateway
negotiates `input_receipts` and `inbox_namespace`, adds the namespace to committed
`run_ack`, and adapts `input_status` reads/waits. At most16 read tasks per peer;
a wait does not block ping/control parsing. Disconnect cancels only those views,
not the input producer. A status read never submits/approves/retries an input.
Only native IDs with the original trusted TUI ReplyTarget can be selected; the
scope/namespace is rechecked after waits. Responses are bounded/redacted, without
raw original attachments.

Claw's actual ChatApp captures a frozen `NativeSubmission` at ordinary submission:
original ID, group, namespace, body and immutable image bytes. It keeps that
submission separate from the editable local queue and current composer. Lost ACK
or a connection-generation change detaches presentation without changing input
identity, merging drafts or reclaiming a running Agent. Captured images remain in
the existing retained-byte accounting.

After reconnect/sync, TUI reads/waits on the same ID. A found receipt is display,
not execution. Terminal outcomes retire only that input and allow unrelated
queued inputs to proceed. Positive absence permits reoffering **the original ID,
body and images**, only in the original namespace and without prior ACK proof.
An acknowledged-but-missing row, namespace change, wrong/stale response or read
failure preserves the original without automatic replay. A late ACK is acceptance
knowledge, not a new live execution binding. Existing Agent steer/follow-up
consumed/returned recovery is separate and never resent as an ordinary input.
No message or recovery approval dialog is introduced; tool permissions unchanged.

## Evidence

- `test_native_receipts.py`: actual Unix server, Core SQLite store and deterministic
  model, unread ACK followed by running/terminal queries, concurrent ping, waiter
  disconnect, strict identities/bools/namespaces, missing row without mutation,
  original workspace change during a wait, wrong transport binding, capacity16.
- The same file drives **real ChatApp + actual Unix frames** across ACK loss:
  the saved native ID is queried, the answer is displayed, exactly one model turn.
- `test_native_submission.py`: independent active/queue/composer boundaries,
  original image/ID reoffer only after absence, prior ACK contradiction, namespace
  change, stale/wrong receipt, input while reconciling, late ACK/new generation,
  terminal failure/explicit and host abort preserving unrelated queue progress,
  and separate conservative Agent-lane recovery.
- Final focused Native/TUI/transport set: **64 passed**. Strict eight namespaces:
  **575 source files**, no issues. Ruff packages and diff check passed.
- Full8 root runner: **6864 passed,34 skipped**, Claw **1192 passed**,136.51s.
  Log under the owned data-disk test root recorded in the plan ledger.
- Flow examples: **17 passed**; `uv lock --check` passed.

An earlier root run hit root-filesystem ENOSPC in the unchanged child access test
`test_real_gateway_child_access_and_replayed_start_receipts[inherit-True-False]`.
It passed alone on the data disk, and full root was repeated successfully using
an owned TMPDIR on `/data`; no user file/cache was deleted or permission weakened.
Earlier test-only helper defects and their corrected runs are recorded in the
plan ledger, not hidden as successful full-suite evidence.

## Remaining boundaries

Local queued/unsubmitted drafts and staged identity are still process-memory
state, not an automatic private disk spool. The existing explicit parent-draft
feature is unchanged; no per-keystroke plaintext persistence is silently enabled.
The subsequent sole-controller/busy-intake cutover is now documented in
`docs/claw-group-controller-verification.md`: online busy intake commits through
Core and controls bind the original actual execution attempt. This earlier
milestone's counts remain historical, not the current final test count. Observer-created callback destinations, final controller cleanup,
optional standalone Goal, two host adapters and shared Goal extension UI remain
open. No goal completion, publish, push or merge is implied.
