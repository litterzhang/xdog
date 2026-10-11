# UI-043 — reviewed compaction after an original parent producer

Checkpoint: October 11, 2026, +08. Implementation frozen by this owner at 16:53;
subsequent work is verification/audit or a separately confirmed regression fix.
This audit does not close the whole UI-043 requirement or replace main's final
frozen-source workspace/terminal gates. The central ledger was not edited.

## Contract and acceptance status

**Implemented and locally verified:** original-parent tool, pre/post, input, event,
and ordinary context callbacks may request the existing canonical compaction
pipeline under an explicitly supported host. The original parent-command path is
unchanged. There is no second agent/tool loop and no user prompt synthesized from
the request.

The public API remains `request_compaction(instructions='', timeout=...) -> bool`.
For the new invocation scopes the internal session operation carries
`operation='compact', after_run=True`. True means reviewed/staged, **not** summary
execution, history replacement or completion. Default/headless/unsupported hosts
return False. Provider/auth/custom callbacks and compaction's own preparation or
committed-observer hooks cannot recursively request this capability.

**Child scope:** no canonical independently owned child compaction host exists in
this slice. Such requests are explicitly unsupported; they do not borrow the
parent's history, UI, producer or summarizer.

## Two independent admission gates

1. `compaction_request.AfterRunCompactions` reserves at most one original-owner
   proposal/review. It retains the reviewed canonical instruction string, not a
   mutable renderer-supplied value. A full false-default `ExtensionNavigation`
   review displays the original session/callback and explains that the entire
   producer must settle. Existing pagination/focus/keypress confirmation rules
   remain in force.
2. The original `InvocationLease.defer` stages the proposal only on successful
   callback completion. Callback exceptions, cancellation, and tool `isError`
   discard the reservation. Callback success alone never starts a summary.
3. `take()` requires the original producer's actual settlement and successful
   outcome, plus the current source/worker/host/capability. It consumes the proposal
   once before canonical admission. Changed/failed/canceled producers, new input
   producers, retired extension owners, changed model/tool profile or session/branch,
   and capability withdrawal discard it without replay.

Targets, sessions and branches are not caller-selected. Instructions use the same
bounded canonical `summary_instructions` contract as existing compaction. A delayed
old callback/discard cannot remove a successor proposal.

## Actual producer barriers and canonical host dispatch

- **Coding native:** each actual `_async_agent_turn` records its original source;
  the queue-worker result is recorded separately. The UI waits for the original
  thread to terminate, including `asyncio.run` cleanup, rather than treating an
  early `turn_end`, cleared streaming flag, or callback reply as settlement. A
  subsequent local queued turn changes the producer and discards the old proposal.
  Dispatch uses the existing `/compact` command machinery and session compaction.
- **Coding RPC:** a parent prompt scope records the actual task and canonical
  `_handle_prompt` outcome. Its done callback also awaits the owning Agent's
  `_producer_task`. Only then can the existing `RpcNavigation` compact action
  reserve the idle host. Pre-turn/finalization errors do not become successful
  compaction triggers just because the RPC adapter emitted an error and returned.
- **Claw:** a connection-scoped original-run marker binds the actual session in
  the orchestrator. Dispatch waits for the outer gateway task, Agent producer,
  final pending writes, and original group-reservation release. It then acquires
  a fresh group reservation and invokes `Orchestrator.compact_session` and the
  existing session pipeline. Intervening queued/group input or changed source
  generation cannot be summarized under the earlier review.
- **Commit boundary:** Coding's canonical `compact`/`_run_compaction` gains a
  backwards-compatible optional `alive` guard, checked before preparation, before
  summarization and before committing a replacement. Existing one-argument call
  sites remain one-argument calls. Claw already supplies the corresponding guard.
  Existing admitted-commit shielding/recovery semantics remain: cancellation after
  the point of no return is not described as rollback and is never auto-retried.

Model/permission policy is unchanged. Existing canonical Claw memory flush and
summary behavior is not replaced or duplicated. The new host entry gates deny a
request while that session's own compaction is already running.

## Negotiation, observation and interoperability

- `extension_compaction_after_run:1` is a **separate default-off** RPC/Claw
  capability with its own generation. Legacy `extension_compaction:1` does not
  silently grant tool/hook compaction. Claw after-run support also works while
  command-only extension compaction is explicitly disabled.
- Capability revocation cancels only this owner's after-run compact operation and
  dialogs satisfying **both** `operation == 'compact'` and `after_run == True`.
  Other after-run name/tool-selection reviews and independent user compactions are
  not canceled by this capability. This has a dedicated RPC/Claw regression test.
- Native/RPC producer records and Claw's original-run marker remain available with
  compaction disabled. They are **read-only lifecycle observations, not authority**.
  The naming and active-tool owners reuse them under their own independent grants,
  source/CAS checks and queues. Claw's marker records its session/task/outcome even
  when `bind()` returns no compaction handler.
- Claw uses normal `session_compact_result` receipts after an owned started notice;
  its native client reuses the existing pending/cancel/result lifecycle. It never
  sends a second synthetic compaction request just to execute the reviewed one.

## Changed seams

New implementation modules:

- `packages/agent/src/xdog/agent/extensions/compaction_request.py`
- `packages/coding/src/xdog/coding/modes/interactive/after_run_compaction.py`
- `packages/coding/src/xdog/coding/modes/rpc/after_run_compaction.py`
- `packages/claw/src/xdog/claw/core/runtime/after_run_compaction.py`

Authorized narrow shared edits: API method/feature bit, runtime routing, worker
bit, session request validation; Coding native producer/compact branches, RPC
binding/capability/outcome/compact branches, canonical Coding compact guard; Claw
compact-only connection/gateway/orchestrator/TUI hooks. Existing message, queue,
auth, naming and editor edits were preserved and their owners coordinated.

A default-off `XDOG_RPC_COUNT_FILE` test-fixture option counts actual offline model
invocations without changing the shared fixture's existing `entered` marker.

## Root-config evidence

Commands use existing dependencies only, from repository root:

```bash
uv run --no-sync --offline python -m pytest -c pyproject.toml -q ...
uv run --no-sync --offline ruff check ...
uv run --no-sync --offline mypy --strict -m ...
```

- Focused shared/worker/native/RPC/Claw tests, real JSONL reader and new physical
  matrix: **42 passed**, no skips, **41.70 s**,
  `/tmp/ui043-after-compact-focused-final.log`.
- Existing command/compaction regression group without changing its assertions:
  **74 passed**, no skips, **42.11 s**, `/tmp/ui043-after-compact-legacy.log`.
- Current integrated union: **116 passed**, no skips, **93.13 s**,
  `/tmp/ui043-after-compact-acceptance-current.log`.
- The four physical Linux paths (Coding/Claw × approve/cancel) additionally passed
  standalone: **4 passed**, no skips, **12.89 s**,
  `/tmp/ui043-after-compact-terminal-first.log`. They prove full visible review,
  narrow resize, exact original+summary invocation count, no summary on cancel,
  editable Unicode parent draft/cursor, main-buffer and termios recovery.
- Actual JSONL parent-run reader with new capability on/off: **2 passed**,
  **9 deselected**, **5.64 s**, `/tmp/ui043-after-compact-jsonl-second.log`.
  The original `message_end` precedes canonical compact completion; actual model
  counts are exactly one without the new grant and two with approved compaction.
- Scoped Ruff is clean; strict mypy **16 affected production modules clean** (only
  unused configuration notes): `/tmp/ui043-after-compact-ruff-final.log`,
  `/tmp/ui043-after-compact-static-final.log`.

The explicit Claw “after-run on / old command capability off” case was added after
that integrated run; its final result is recorded below, not silently included in
116. Source hashes are a timestamped shared-worktree checkpoint, not a claim that
other owners' disjoint regions remained immutable after the run.

### Retained failures and limits

The first focused run exposed a missing dedicated-handler `has_ui` projection and
an incorrectly authored fixture event signature; both were corrected. The first
JSONL test assumed the existing `entered` marker was an integer counter; an explicit
opt-in real-call counter was added instead of weakening the count assertion.
An integrated run during concurrent shared-file edits had **105 passed, 11 failed**
with extension bootstrap/context failures. It is retained as failed evidence in
`/tmp/ui043-after-compact-acceptance.log`; an immediate exact RPC/Claw recheck passed
2 cases and the full current union subsequently passed 116. No previous failure is
presented as a pass. A nonfatal existing `aiosqlite` event-loop deprecation warning
appeared after one successful physical run.

No dependency downloads, live authentication/provider requests, paid calls,
publication, user-service restart or central-ledger edits. Tests use only owned
fixtures and private temporary gateways/PTY/tmux instances. No OS sandbox, external
provider compatibility, missing platform environment, or whole-UI closure is claimed.

Final independent Claw capability case: **1 passed**, no skips, **2.48 s**,
`/tmp/ui043-after-compact-claw-independent.log`. Production/test source checkpoint:
`/tmp/ui043-after-compact-source.json` (23 files, captured 16:57 +08).
