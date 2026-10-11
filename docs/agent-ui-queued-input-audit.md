# UI-043 bounded queued-input extension work (2026-10-11)

This is subordinate evidence, not a central-ledger update or a UI-043 closure.
The authoritative requirement remains in `agent-ui-requirements.json`.

## Grounded scope

The pinned Pi `types.d.ts` in `/tmp/ui036-reference-20261011/` declares
`hasPendingMessages(): boolean`, custom `sendMessage` with `steer`, `followUp`,
`nextTurn`, and `sendUserMessage` with text/image content and `steer`/`followUp`.
No arbitrary extension queue-body get/edit/reorder interface was identified there.
Native UI queue editing remains its independent existing contract.

- `request_queued_input`: explicit review; nonempty bounded text and up to four
  inline typed images; at most 6 MiB serialized proposal, at most 4 MiB decoded per
  image. UI review contains only MIME/byte-count/SHA-256 descriptors, never binary.
- `reviewed(extension, dialog, *, ask, apply, alive) -> bool`: True means staged,
  not admitted. `apply(id, labeled_text, delivery, typed_images)` runs only after
  the originating callback succeeds and its owning host remains active/streaming.
- Native/RPC parent bridges are MAIN-owned. Claw host bridges are Claw-worker-owned.
  Both child factories bind the actual child Agent, not its parent. Queue commits
  and live reads bridge to the original factory loop using `settle_on`.
- The child capability is independently negotiated and false by default:
  `child_extension_queued_inputs`. Token/generation, runtime, session, model,
  original Agent and streaming-state guards fence review/admission. Detach revokes
  pending reviews. Socket hosts can register an async receipt sender; callback
  completion waits for delivery, and failed delivery removes only its own still-
  unconsumed ID. A successful removal emits a negative terminal notice, not a
  fabricated rollback of already consumed work.
- `has_pending_messages()` returns a **live** own-Agent boolean, or None when
  unavailable. It is a bounded reverse read with a separate host callable/flag,
  not an invocation snapshot and not a queue body/ID/order export. It grants no
  mutation or ordinary dialog authority.

## Callback matrix

| Callback/surface | Reviewed active-run queue | Live pending boolean |
| --- | --- | --- |
| Tool / pre / post / submitted-input / event callback | Explicit host grant + review + original callback success + active run | Separate original-Agent callable |
| Owning command | Not an active-run queue grant; existing idle message feature is separate | Harmless owning status read, callable required |
| Context transform | Not granted queue mutation | Separate owning callable |
| Terminal interception | Not granted | Not granted |
| Provider callback | Not granted | Not granted |
| Custom rendering / custom input | Not yet implemented for this queue API | Not granted |
| Headless with no registered host | No imaginary review/queue surface | None without owning read callable; explicitly bound boolean read needs no UI |
| Independent child | Original child binding/capability only; never borrow parent queue | Live original-child loop only |

Custom message content/nextTurn semantics remain an explicitly separate applicable
requirement. This work does not claim full Pi API/wire equivalence or UI-043 closure.

## Canonical child recovery

`ChildSupervisor._settle` waits for the Agent, drains pending input to `undelivered`,
then invokes its finalize hook and persists the final record. Therefore the child
extension observer must **not** drain on `AgentEndEvent`. It emits notices only;
canonical supervisor recovery retains unconsumed text/images. Cancellation need not
emit AgentEndEvent, so finalize also emits an uncertainty notice for outstanding
IDs without a consumption receipt. It does not manufacture a parent draft or
perform automatic replay. The focused abort test inspects both the supervisor's
actual record and `ChildStore.load()` for the original typed image.

## Owned hung-test incident: precise identity and cleanup

Only the following diagnostic was interrupted:

- Command: `UV_OFFLINE=1 uv run --no-sync python -m pytest -q
  packages/agent/tests/test_extension_pending_messages.py`
- Tool session **4567**; pytest PID **1263960**; launcher PID **1263952**.
- Test stuck after the first passing test, in
  `test_live_boolean_cannot_adopt_replacement_handler_or_session_after_await[reader]`.
  The test used `asyncio.Event` objects across the pytest loop and the extension
  manager's thread/loop. The host-read callback awaited an event on the manager
  loop, while the test set it from the pytest thread without a thread-safe wakeup.
  This was an invalid cross-loop test synchronization primitive, not a normally
  progressing slow suite or evidence of unrelated service failure.
- A **single SIGINT** was sent to the identified pytest PID (`kill -INT 1263960`),
  not to a process-name pattern, server or other suite. The captured log
  `/tmp/ui043-pending-shared-first.log` ended with KeyboardInterrupt and
  `1 passed in 84.89s`; polling the original tool session confirmed **exit 2**.
  Subsequent process inspection found neither PID nor that test command alive.
  Test finally/owned runtime shutdown was allowed to execute; no live service
  restart, arbitrary process kill, dependency install or worktree reset occurred.
- The test now uses thread-safe markers and async polling. The production live
  reader also has an explicit bounded timeout/unavailable result, so a genuinely
  unresponsive host read cannot wait forever. The corrected pending/read-review
  group passed **31 tests**, `/tmp/ui043-queue-review-pending-current.log`.

No other progressing verification process was interrupted. Other failed logs are
retained: early tests had a false expectation that Escape returned None rather
than the review's explicit False; early child image checks exposed an actual
missing same-model live metadata lookup, then a corrected `ModelStreamFn` import
location; an abort test initially tried to replace a checkpoint during an active
run and now installs its checkpoint while the factory Agent is idle. These are
reported separately rather than reclassified as a completed full gate.

## Canonical identified admission (authorized follow-up)

Child admission no longer contains a second queue implementation. The ordinary
`steer`/`follow-up` control path and the internal extension identified-input seam
both call `ChildSupervisor._queue_admission`. The seam requires the original
child and Agent, an `extq-` host-generated ID, and the original host's live
predicate. Public external controls cannot supply an input ID.

This shared path preserves the existing 32-input, text-byte, image collection and
retention bounds; writes the prospective queue **before** live admission; owns
`queue_work`; settles an actual writer on cancellation; and rechecks child/Agent/
streaming/host ownership after storage. Both client factories provide admission
and discard callbacks; optional bindings without those callbacks fail closed.
Socket receipt failure removes only its own unconsumed ID through the canonical
journaled queue-removal path. If durable removal fails, the original child is
aborted rather than allowed to silently consume an input whose receipt failed.
No rollback is claimed for selected/consumed input.

Current canonical-focused execution: **54 passed, no skips, 36.47s**,
`/tmp/ui043-child-canonical-current.log`. It includes the full existing
`test_child_input_journal.py`, the new
`test_child_extension_input_journal.py`, and actual RPC child model/tool queues.
Coverage includes both old/new admission paths, exact original-loop enqueue,
journal failures before/after write, canceled writer settlement, owner revocation,
foreign Agent/ID rejection, durable own-ID removal without touching manual input,
and a real Linux owned SIGKILL after an identified-image admission receipt.
Restart inspection restores an interrupted child with the typed image and does
not invoke a factory or automatically replay input.

The earlier canonical gate's only failure was a test calling the private dequeue
helper with its singular rather than existing plural name; that test was corrected
and the full gate above rerun. This did not require a product behavior change.

The shared/reviewer/live-read/child gate immediately preceding the canonical
refactor passed **109 tests** (`/tmp/ui043-queue-shared-child-verified.log`). The
current all-seven strict type check passes **538 source files**, with focused Ruff
clean (`/tmp/ui043-queued-current-static.log`). A fresh combined host/storage/
physical-terminal gate is in progress; its final result will be recorded below.
Additional cleanup evidence for the identified hung diagnostic is
`/tmp/ui043-owned-reader-cleanup.log` (neither recorded PID nor the blocked test
identity present).

## Final shared/host checkpoint for this slice

The combined process exited **0** (tool handle **99894**): **233 passed, no skips, 213.42s**,
`/tmp/ui043-queued-current-integrated.log`. This single run includes shared request
validation and callback lifecycle, live boolean inspection, whole-proposal reviewer,
old/new canonical journal paths, current RPC child queues, native/RPC parent queued
inputs, Claw parent/child UI and transport ownership, and both hosts' queued-input
physical terminal tests. Host implementations/tests authored by MAIN and the Claw
worker are included as integration evidence, not attributed as this worker's edits.

This queued-input/live-read/original-child/durability slice is ready for integration.
It does **not** close UI-043 as a whole: custom message/nextTurn semantics and any
broader original host contract must be reconciled by MAIN against the authoritative
ledger. No arbitrary extension queue-body inspection/edit API was invented, and
no current-Linux durability requirement was deferred.
