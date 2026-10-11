# UI-043 — non-triggering custom context / nextTurn contract

Checkpoint: October 11, 2026. This is a bounded sub-contract audit, not a new
ledger ID or a claim of arbitrary renderer parity. Main retains central status
and final whole-root integration. The verified local reference is Pi 0.73.1,
`/tmp/ui036-reference-20261011/extensions.md:1268` and `types.d.ts:833`: custom
messages carry customType/content/display/details; nextTurn waits for the next
user prompt without interrupting or triggering a turn. `appendEntry` is separate
extension state that does not participate in LLM context.

## Public request and ownership

`ExtensionAPI.request_context_message(custom_type, content, *, display=True,
details=None, deliver_as='next_turn', timeout=300) -> bool` supports exactly this
non-triggering delivery contract. Content is text or a tuple of typed TextContent /
inline ImageContent values; details are bounded opaque JSON. There is no new
steer/follow-up/trigger-turn variant. Parent command/tool/event/pre/post/input
callbacks can request it only when the original host supplies the capability.
Children, rendering/provider/setup callbacks and unsupported hosts are explicitly
unavailable; no parent queue or session is borrowed.

The shared `ContextMessages.review(extension, dialog, *, ask, alive,
schedule=None)` helper projects a binary-free whole-proposal review with image
hash/size/media descriptors and false-default consent. ExtensionContextMessage
uses the existing full-paint form machinery. The host supplies original session,
branch, connection/run/view authority; extension fields cannot select those.

Approval stages work under the actual `InvocationLease`. Callback failure,
`isError`, cancellation, ownership change or failed parent invocation discards
uncommitted work. Only successful original callback completion may enqueue the
reviewed record. A scheduled commit is consumed once before awaiting, including
failed/uncertain commits; there is no automatic replay.

Optional `schedule(commit_callable)` is an async host adapter. Native/tool paths
execute the commit directly at callback success. Main's RPC command adapter holds
that callable until the **complete successful extension_result line** is delivered,
then executes it with original scope/generation checks. Failed/withdrawn output
cannot invisibly persist nextTurn context. The RPC controller does not implement a
second persistence store.

## Explicit-user admission watermark

The SDK/Claw host captures `(session/branch scope, immutable pending IDs)` **before**
initialize/turn_start/on_input lifecycle callbacks. A request staged during those
callbacks or the active turn is absent from that watermark and waits for the
following explicit user prompt.

`Agent.prompt(..., initial_prompt_context=callback)` invokes this one optional
callback only after successful transformation of the original initial UserMessage,
with cancellation checks and an owned cancellation watcher/task join. Handled or
rejected input does not invoke it. Queued replacements, steer/follow-up, continue,
tool iterations and internal retries do not inherit it. The existing tool loop
and permission boundaries remain unchanged.

Coding `AgentSession.send_message(..., input_origin='user')` captures only for
`user`; `extension` and `system` do not. Main also wired the native direct-Agent
turn path and RPC command-message origin path. Claw captures only for UserInput
with user origin, never SystemInput scheduler/goal work. Its extension-requested
message origin comes from the gateway's validated internal grant, not supplied
user metadata. A queued real user prompt is a fresh explicit-user boundary.

## Canonical model and display contract

Delivery persists `CustomAgentMessage(type='xdog.extension_context.v1', data=...)`
with unique ID, extension, session, branch, invocation and callback-method
provenance plus custom_type/content/display/details. This is not an appended
extension entry, metadata pretending to be a user transcript row, or a new user
prompt. The existing message codec round-trips the custom record.

Only this exact canonical type is projected by the default Agent/loop converters.
Its validated content becomes provider-compatible content with an explicit
user-reviewed extension-context label. **Opaque details are never model text.**
Other CustomAgentMessage types remain filtered. Pending images obey the host's
current advertised model/image limits before canonical admission; refusal retains
pending context without calling a model or claiming uncertain disk mutation.

`display=False` suppresses ordinary UI/source display, not model context. Live
native display and shared transcript display use explicit custom/system provenance,
never a user-role impersonation; details do not become prose. Canonical export and
persistence retain metadata subject to existing export redaction policy.
Compaction now recognizes only this canonical type: both clients summarize its
content/provenance independent of display, exclude details and raw image bodies,
and leave arbitrary custom metadata filtered. Original image bytes remain in the
archived canonical source, not a fabricated textual image description.

Claw's upcoming user source-channel slot shifts past inserted custom records, so
`tui`/other input channel provenance stays on the actual user message rather than
being assigned to custom context.

## Persistence and cancellation

The shared store uses a private JSON sidecar keyed by a hash of the original
session/branch. It is bounded to 32 pending records / 16 MiB; individual content,
images and details have explicit smaller limits. Session/branch changes never
silently move pending state. Ordinary prompts without pending files create no
sidecar directory/lock.

- Successful callback commit writes/fsyncs a temporary file, rechecks cancellation
  and original ownership immediately before atomic replacement, then fsyncs its
  directory on POSIX.
- Disk tasks are shielded and joined. Cancellation sets a thread-visible flag;
  held pre-replace fsync cannot result in a later unowned replacement or success
  publication. A replaced record is not rolled back. Post-replace errors/cancel
  publish a scoped failed/uncertain receipt and block that live scope until reload.
- Same-path adapters share a nonblocking transaction lock; POSIX additionally
  uses a process file lock. The admission transaction remains owned through the
  original-loop canonical-history persistence callback and pending retirement.
- Canonical history is persisted before pending removal. On restart, pending IDs
  already present in canonical history are retired without another delivery.
  Failed/uncertain live history writes are not overwritten by end-of-turn saving.
- Coding uses the real atomic SessionManager snapshot path; Claw uses atomic
  transcript replacement while preserving stored metadata and source indexing.
  No second agent/model loop or external exactly-once effect is claimed.

Tests inject blocked fsync/pre-replace cancellation, owner changes, concurrent
adapters, and history-before-retirement failures. They exercise real persisted
SDK/Claw reloads; they are **not physical power-loss or OS SIGKILL evidence**.
Non-Linux native storage/terminal validation remains deferred, not inferred.

## Files / coordinated seams

New production modules:

- `packages/agent/src/xdog/agent/context_messages.py`
- `packages/agent/src/xdog/agent/extensions/context_message.py`
- `packages/coding/src/xdog/coding/core/context_messages.py`
- `packages/claw/src/xdog/claw/core/runtime/context_messages.py`
- `packages/tui/src/xdog/tui/components/extension_context_message.py`

Bounded existing-module changes: Agent prompt/default conversion; extension
API/runtime/worker capability routing and session-dialog validation; Coding
AgentSession persistence/admission; Claw session/orchestrator original command/run
binding, gateway/TUI context-only capability/generation/review paths; shared custom
transcript display; the two explicitly approved compaction projection branches.
Other workers' queue/editor/auth/catalog modifications were preserved using
fresh-read, unique-section edits. No central ledger was edited.

New dedicated tests owned here:

- `packages/agent/tests/test_context_messages.py`
- `packages/coding/tests/test_context_message_persistence.py`
- `packages/coding/tests/test_context_message_compaction.py`
- `packages/claw/tests/test_context_messages.py`
- `packages/claw/tests/test_context_message_terminal.py`
- `packages/tui/tests/test_context_message_contract.py`

Main owns the shared contextrequest fixture, Coding native/RPC/physical host tests,
RPC scheduling controller, native/RPC origin propagation and display hooks.

## Evidence and handoff

All commands run from repository root with `uv run --no-sync pytest -c
pyproject.toml`; no installs, paid models, browser/auth calls or user-service
restarts. Private gateways/tmux instances and fixture workers are test-owned.

- Core/disk/actual SDK-Claw persistence/native/shared display: **36 passed**, 20.22s,
  `/tmp/ui043-context-final-unit.log`.
- Content-only compaction + actual current model/image metadata limits:
  **12 passed**, 10.32s, `/tmp/ui043-context-compaction.log`.
- Combined Agent baseline/input/producer ownership, new context contracts, both
  real native hosts, main RPC context tests, SDK/session and Claw compaction
  regressions: **101 passed**, 50.79s,
  `/tmp/ui043-context/final-focused.log`.
- Final scheduled-commit/scoped-uncertainty and compaction type-alias fixes:
  **33 passed**, 10.68s, `/tmp/ui043-context/final-fixes.log`.
- Strict Agent/Claw/Coding/TUI mypy: **417 source files, no errors**,
  `/tmp/ui043-context/final-types.log`. Changed-file Ruff was clean; the earlier
  compaction local/import name collision was fixed and its tests rerun.
- Main's Coding native/RPC first gate: **27 passed**, 44.90s,
  `/tmp/ui043-context-hosts-first.log` (overlapping evidence).

Final physical Coding **2 passed**, 4.75s (`final-terminal.log`), and physical
Claw **2 passed**, 5.52s (`final-claw-terminal.log`): true/false review, no model
before the next explicit user, canonical persisted custom records, current Linux
resize/full-paint and parent input/main-buffer/termios recovery. The final Claw
source-channel correction is covered by **8 passed**, 12.97s
(`final-claw-source.log`); it keeps input provenance on the actual user after
custom records are inserted. Final strict affected namespaces remain **417 source
files, no errors**. Two physical-test line-length wraps were the last static-only
fix; final current Claw terminal/static results are in `last-check.log`. These
counts overlap and must not be added into a synthetic total. No full-workspace
freeze/regression claim is made while other owners are finalizing unrelated seams.

Recommendation: use this evidence for the finite UI-043 custom nextTurn contract,
not as closure of every other UI-043 API or the whole goal. Main retains final
root/static/build/examples/Linux lifecycle integration. The separately observed
intermittent Claw nested-completion timeout remains a lifecycle finding; it is
not erased by green context tests.


**Handoff recommendation:** the assigned non-triggering canonical custom
nextTurn contract is implemented and covered by the named core, actual-host,
restart/disk, summary, metadata/image-limit and current-Linux gates. No additional
context API is requested. Central UI-043 status and whole-goal completion remain
main-owned; existing unrelated nested lifecycle findings still require final
integration handling. No unverified non-Linux result, physical-power-loss claim,
or additive test-total claim is made.
