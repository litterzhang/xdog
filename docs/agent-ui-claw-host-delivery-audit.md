# Claw reviewed host delivery addendum (2026-10-11)

This addendum records the bounded Claw host implementation, not closure of UI-043,
UI-041, UI-044, or the central requirements ledger. Production edits for this work
are confined to `core/runtime/{orchestrator,gateway}.py` and
`channels/tui/tui_app.py`. Separately authorized agents own shared APIs, child
factories, context-message persistence, after-run compaction, menu reads and the
cold MCP browser fix. Their changes are not claimed as independently implemented
or fully verified by this addendum.

## Requested new messages

`request_message` remains an idle original-parent-command operation. Claw separately
negotiates `extension_message` and an integer generation, default off. The native
whole-message form is false by default. The host constructs the provenance using
`host_message(extension, text)`, preserving whitespace and literal slash-command
text. A transport acknowledgement is not broker acceptance.

After actual broker acceptance, the original callback must finish successfully.
Only successful complete command-result transport (`drain`, without swallowed
transport errors) publishes a bounded, expiring, single-use connection token.
The token retains host-produced text and the original session, Agent, source and
extension generation. The frontend sends the token through the ordinary gateway
`message` path. It cannot supply replacement content, an origin, attachments or a
foreign queue scope. Admission consumes the token, rechecks the owner after awaits
and at the canonical orchestrator lock, and does not auto-reset to a different
session. Busy, stale, expired, foreign and withdrawn grants cannot be replayed.

No slash parser, composer read, attachment capture or second tool loop is used.
The new turn retains normal configured Claw permission and tool-selection hooks.
The native composer and cursor remain untouched, including failed/uncertain
extension delivery; requested text is not automatically restored as a user draft.
Host-only `UserInput.metadata.input_origin=extension` identifies this path so it
cannot consume another extension's next-explicit-user-turn context proposal.
Ordinary socket callers cannot supply that private marker.

## Write-only editor proposals

`extension_editor_write` is independent of old command editor read/replace grants.
Its generation and connection owner bind full proposal review to the real callback
lease. `EditorWriteReviews` retains the original text/cursor/attachment identity
privately; only approval and an opaque revision go back to the worker. Commit runs
after successful callback cleanup and only if the same original editor and snapshot
remain. False, failure, error, withdrawal and changed text/cursor/images discard.
Lost commit publication produces a failed command receipt rather than an apparent
success, and stale reviews are pruned without clearing unrelated live reviews.

Parent native command, tool, lifecycle event, pre/post, input and context paths use
the original operation's handler, restored at settlement. Child proposals use
`ChildExtensions`' independent original factory binding and may replace only the
selected child's unchanged steering/follow-up draft. No selected original child
form means refusal, never fallback to the parent or a sibling editor. Attachments
stay in place; these proposals never submit text or approve tools. Legacy editor
reads remain denied in non-command callbacks.

## Active-run queues

Parent `extension_queued_inputs` and child `child_extension_queued_inputs` are
separate, default-off negotiated capabilities. Both use the shared successful
invocation lease, not an idle command or another active producer. Review carries
host-labeled text and recomputed image type/byte-count/SHA-256 descriptors, never
inline binary image bodies. Model/profile image admission is checked again before
queue mutation.

Parent additions use the canonical `Orchestrator.control_input` and the existing
connection/run input-ID prefix. Queue receipt delivery settles before the callback
releases the producer checkpoint. Failure/cancellation removes only the original
unconsumed extension ID. Capability withdrawal cannot cancel an unrelated turn or
remove a user's queued input. The native queue shows accepted entries and uses the
normal identified input controls. Consumption, rejection, return and uncertain
settlement do not copy extension text/images into the unsent parent composer or
silently replay them. Existing recovery of the original user-submitted prompt on
an ordinary run failure remains unchanged.

Child additions use the shared original child Agent/factory loop and its own model,
profile, queue and capability generation—not the parent `control_input` path. An
awaited, per-token native receipt sender bridges to the original socket loop;
failed transport cannot release the callback checkpoint with its extension input
still queued. Negative receipts distinguish actual successful removal from an
uncertain/selected input. Child returned-input notices do not populate the parent
or child composer; durable unconsumed recovery remains the canonical child
supervisor's responsibility. Native receipts identify the exact child and refresh
its existing queue viewer when applicable.

Original-owner `has_pending_messages` boolean reads are loop-bound and expose no
queue contents. Parent turn/command bindings are restored afterward; child reads
come from the original child factory. Queue mutation remains unavailable to idle
commands, provider/terminal/render contexts, and unbound/headless mutation scopes.
No unsupported scope borrows a parent producer.

## Native provider authentication

Claw adds independent `extension_provider_auth` negotiation, an original auth
operation owner and generation, and `/extensions auth`. `/login [EXTENSION_ID]`
uses the shared alias parser: an empty argument opens a provider/action menu with
Cancel first; it never chooses a default login. Native `xdog-ai` provider login is
a different interface.

The auth action reserves the original idle group/session. A cold explicit native
request may create its authoritative session, and only that request's previously
empty native owner adopts the resulting broker session ID. An old client lacking
the auth capability cannot bootstrap or execute auth. Only the active auth host's
private lease/digest-marked dialogs use the sensitive renderer; a plugin marker
cannot obtain that privilege. Login, challenge and code entry all require full
review. Code is held in a separate masked buffer, erased on completion, cancellation
or capability withdrawal, never normal editor undo/history or public snapshots.
URL opening requires a separate fully painted explicit gesture; no browser is
opened automatically. Native storage needs separate exact backend/namespace
consent; denial does not call a credential-store service.

Failed auth-view transport resolves the original broker rather than leaving an
indefinite busy owner. Withdrawal cancels only this connection's auth operations.
Normal messages, model selection, tool policy, draft text and attachments are not
authorization side effects. Credential/storage settlement remains governed by the
shared provider auth controller; this is not a promise to roll back an already
committed credential-store action after a lost acknowledgement.

## Additional assigned terminal integration

The gateway negotiates `terminal_release_input` independently. Native release
observation requires that grant and an explicit `release` event declaration from
the selected original parent or child. Child selection never inherits the parent's
terminal event declaration. This work only wires the shared interceptor/protocol;
it does not duplicate their event decoder or create terminal mutation authority.

## Checkpoint evidence and limits

All commands use the existing environment, repository-root pytest configuration
(`uv run --no-sync pytest -c pyproject.toml ...`) and offline fixtures. No dependency
installation, live provider/auth/browser, publication, credential-store service or
user-service restart was performed. Private tmux servers were created only for the
requested physical Linux terminal tests and were torn down afterward.

- Requested-message/release/related native regression: **123 passed, no skips,
  117.70s**, `/tmp/ui043-claw-message-scoped-final.log`.
- Editor-write plus message/release/legacy editor/nested/native regression:
  **169 passed, no skips, 204.59s**,
  `/tmp/ui041-claw-editor-message-integrated.log`.
- Parent/child queue, canonical input/image/queue, and cold MCP owner regression:
  **93 passed, no skips, 95.68s**,
  `/tmp/ui043-claw-queue-integrated-final.log`.
- Provider auth native/socket tests: **14 passed, 7.93s**,
  `/tmp/ui044-claw-auth-expanded.log`; cold physical menu acceptance and private
  prompt cancellation: **2 passed, 5.63s**,
  `/tmp/ui044-claw-auth-physical.log`.
- Physical counts included above: requested message 2; editor parent/child 4;
  parent/child steer/follow-up image queue approve/deny 8; provider auth 2.
  Physical checks include full review after resize, no premature model/auth action,
  no secret terminal bytes, cursor/main-buffer and termios restoration.

These are scoped implementation checkpoints in a shared dirty workspace, not a
frozen complete-root acceptance claim. Intermediate harness failures were retained:
wrong pytest import root, wrong offline event constructor/unresolved result future,
inspection of an already-disposed child Agent, and a fixture command duplicated
while its shared source changed. They are not promoted to production defects.
The lost editor-commit publication leaving a command without a final result was
fixed in the host and has a dedicated transport regression. Shared child-factory
import/receipt issues were reported to their owner, not patched by borrowing parent
execution. Broader context/after-run/menu reconciliations and final root/type/build/
example gates remain the main task's responsibility.

### Final bounded idle-message image addition

`extension_message_images` is a separate default-off capability/generation. Text-only
requests do not need it. The shared `PreparedMessage` validates explicit inline
`ImageContent` values off-loop, retains the immutable typed tuple privately, and
projects only host-labeled text, byte/MIME/SHA-256 descriptors, a review identity and
a digest. Image-only proposals are supported; an empty text-and-image proposal is
not. The original model must accept the images before review and again at canonical
message admission. Changed model support, image-capability withdrawal/re-enable,
callback failure or complete-result transport failure sends nothing.

The existing one-shot token holds those private images beside the text; the frontend
still sends only a token and never supplies uploads or composer images. The native
optimistic image count is metadata, not captured draft attachments. This addition
keeps the original request-message result/owner gates and `input_origin=extension`.
It does not widen queue/context APIs or claim a visual image inspection: the full
review identifies the exact supplied bytes through their descriptors.

Image checks: **10 passed, 9.68s**, `/tmp/ui043-claw-message-images-second.log`;
physical image-only approve/deny **2 passed, 5.37s**,
`/tmp/ui043-claw-message-images-physical.log`; original text-message worker/socket
regressions **43 passed, 49.16s**,
`/tmp/ui043-claw-message-images-text-regression.log`. An initial native handshake
omitted the new flag after a concurrent hello-line split; the real native tests
refused safely, and explicit native negotiation was added before the passing gate.

Additional auth boundary: same-buffer auth request plus withdrawal/off-on initially
lost its result when canceled before coroutine admission (two bounded failures in
`/tmp/ui044-claw-auth-admission-red.log`). Auth-only pre-admission cancellation now
publishes exactly one failed/canceled result and releases the original reservation,
without starting a worker or changing an unrelated command. Final auth gate,
including both physical paths: **18 passed, 13.39s**,
`/tmp/ui044-claw-auth-final.log`.

Before the bounded image addition and that final auth admission fix, the combined
22-file own-host gate was **236 passed, no skips, 260.40s**,
`/tmp/claw-host-own-final-integrated.log`. These later changes have the additional
scoped gates above; that earlier combined log is not substituted for a frozen final
source regression. Current Claw strict typing after image integration is clean over
86 source files (`/tmp/ui043-claw-image-current-static.log`); own host/test Ruff is
clean. After-run tool/name/compaction and context additions are independently owned
and must be included in the main task's shared freeze validation.

### Shared-freeze handoff: do not replace the aggregate failure with rerun success

The later combined 24-file gate, including idle images and final auth admission
handling, finished **249 passed / 1 failed, 320.01s**:
`/tmp/claw-host-own-image-final-integrated.log`. The failure is the existing native
nested-view test
`test_claw_nested_stack_connection_leases_restore_same_parent_and_draft[full-False]`:
after restoring and painting the original parent, its Enter did not settle
`_extension_pending` within the test deadline. No freshness/paint guard was weakened.
The focused file rerun was **8 passed, 13.48s**
(`/tmp/claw-host-nested-rerun.log`), but that does **not** establish a fix or retire
the aggregate failure. A similar earlier plain-parent timeout was already retained
in the renderer owner's audit. Cause remains unproven; the shared main task has the
trace and the diagnostic harness path `/tmp/ui037/test_nested_diagnostic.py`.

The newly requested after-run active-tool family was coordinated with its shared
helper owner and landed only in its tools-specific module/hooks. Its independent
connection capability observes the same original producer task without granting
compaction or naming. Canonical idle durable selection CAS remains the only apply
path. The owner reports **8 Claw tests passed, 15.07s** and **152 combined tests
passed, 99.80s** (`/tmp/ui043-active-tools-claw-first.log`,
`/tmp/ui043-active-tools-integrated.log`); detailed scope and limitations are in
`docs/agent-ui-active-tools-after-run-audit.md`. These separate results do not waive
the nested-view failure or constitute a complete-root freeze gate.

Own production expansion stopped before the requested 17:00 +08 freeze. Final
pre-freeze own-host/test Ruff and strict `xdog.claw` (86 modules) checks were clean
(`/tmp/claw-host-pre-freeze-static.log`). No application service was restarted.

### UI-064 nested completion: confirmed cause and post-freeze repair

The main task explicitly authorized investigating and repairing the recurring
nested-parent timeout after feature freeze. A bounded instrumented full-mode run
reproduced it with 2 ms frame-write latency: 13 passes followed by one failure in
28.22s (`/tmp/ui064/trace-first.log`). Original trace copies are preserved under
`/tmp/ui064/pre-repair/`; the decisive trace is
`/tmp/ui064/confirmed-red-trace.json`.

The final Enter reached the correct gateway and worker. The cause was a stale
negative resize ACK: generation 8's refusal arrived after the frontend requested
generation 12. Because the ACK carried no generation, it cleared the *new* request
and caused a duplicate generation-12 resize. The UI painted revision 3 and sent its
single Enter; the duplicate resize advanced the broker to revision 4 first. The
worker returned `done`, but the unchanged broker correctly rejected completion
because the painted revision was stale, then published `review_required`. This
was not a hung callback or an undrained command result.

The approved repair was applied at **17:06:12 +08 on October 11, 2026**:

- `gateway.py` fully validates the original custom event and echoes only its
  optional bounded exact-integer generation in the corresponding ACK.
- `ExtensionCustom.accept` settles an old-generation refusal's counter without
  clearing a newer viewport request or paint proof. Current-generation and legacy
  generationless refusals still revoke/retry conservatively. Malformed/future ACK
  metadata owns neither the current proof nor its pending counter.

No callback replay, timeout increase, completion guard relaxation or test deletion
was used. The broker, event validator and original failing nested test remain
byte-identical to the main task's pre-repair frozen hashes
(`/tmp/ui064/unchanged-guards.json`). Reversing only these two production edits also
reconstructs their exact frozen hashes (`source-proof.json`, `production-repair.diff`
in the same evidence directory), demonstrating preservation of other dirty work.

Validation: deterministic old-ACK test red→green; **26 targeted tests passed, 17.04s**;
**36 instrumented full-mode/latency repetitions passed, 49.33s**; original physical
nested fullscreen parent/child paths on Coding and Claw **4 passed, 15.11s**.
Post-repair trace `trace-0-0.007.json` observes the same late generation-8 refusal
while generation 12 is current: its pending viewport stays intact, and Enter's
painted revision 3 equals the worker's revision 3. Ruff and strict TUI/Claw typing
(202 modules) pass.

The broader custom regression yielded **355 passed / 2 failed, 229.43s**. Those two
failures are Coding RPC unread-large-frame quit/EOF parent teardown tests, not the
nested completion case; the exact file rerun reproduced **2 failed / 4 passed,
34.26s**. Logs are `custom-regression.log` and `rpc-backpressure-rerun.log` under
`/tmp/ui064/`. The main task assigned that confirmed remaining regression to its RPC
owner. These failures are not waived by the nested repair. The already-running
whole-root pass was intentionally left uninterrupted and is pre-repair evidence;
a new frozen complete-root pass remains required.
