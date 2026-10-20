# Claw Group / Context reliability

## Intent and boundary

Keep Claw a long-lived personal assistant: Group owns identity, workspace, memory,
capabilities and preferences; exactly one main AgentSession executes its current
Context. Historical contexts/checkpoints are read-only evidence. Execution does
not depend on a connected TUI. This is a repair of existing ownership, not a new
multi-session execution framework. No message approval, implicit Goal delegation,
automatic uncertain-effect replay, migration, release or push is introduced.

## Decisions

Prefer targeted invariants and reuse of the existing persistence journal over
(1) a new lifecycle/storage framework or (2) tolerating missing state through
fallbacks. The former expands scope; the latter silently changes the assistant.

### History availability

An existing empty regular transcript is a legitimate new Context. A missing,
unreadable, malformed or nonregular known transcript is unavailable, never empty.
Preparation must check the original Context before automatic rotation, and direct
rotation must not silently hide missing history. Unknown transcript identities
also fail explicitly; unknown branch lookup may retain its documented None result,
whereas a known missing checkpoint is unavailable. Preserve original files and
pointer on failures. Reads use no-follow regular-file descriptors, not an exists
check followed by an unsafe open. IO stays on the existing owned worker.

### Timestamps

Persisted current-context created_at and last_active, plus resumed_at when present,
must be ISO timestamps with an actual UTC offset. Reject invalid/naive values at
read time without rewriting data. UTC and explicit offsets remain supported.
Daily reset remains 04:00 UTC by default; idle reset measures elapsed real time.

### State inheritance

Both automatic daily/idle reset and explicit /new mean “new conversation”, not
“factory reset”. Explicit preference controls change preferences.

| State | Owner / rotation rule |
|---|---|
| Identity, workspace, memory | Group, unchanged |
| Goal/observer storage and lifetime | Capability/Group, unchanged |
| Selected model, model-bound reasoning | Group preference, inherit both reset kinds |
| Selected active tools and skills | Group preference, inherit both reset kinds |
| Access restriction | Security state, inherit without relaxing scope |
| Messages, compaction state, history cursor, child execution identity | Context, fresh |
| Extension entries | Context, fresh (opaque entries may reference old history) |
| Extension configuration | Group/configuration, unchanged |
| Label, counters, timestamps | Context, fresh |

Persist preferences into the new Context under the same journal as its transcript
and current pointer. This preserves current controls/read paths without a second
preference authority or old-data migration. Missing selections mean configured
defaults, explicit empty selections remain empty. Invalid selections fail closed;
tool selection is never authorization.

### Lifecycle

GroupRuntime owns prepare/reset and close of group/session resources. Orchestrator
owns ordering relative to input/output/channels but calls public group lifecycle
methods, not private resources. Close is owned, idempotent under concurrent/repeated
cancellation, settles all resources and propagates errors. Keep existing fencing,
retired-extension weak ownership and child joins. The synchronous preparation path
is test/legacy-internal only; align invariants rather than adding another runtime.

### Compaction

Commit the full original checkpoint (transcript, sidecars, catalog) durably before
destructive history replacement. Reuse NavigationJournal for checkpoint and
replacement transactions, including directory fsync and cache publication after
commit. A replacement failure may leave a valid extra checkpoint, not a success
receipt. Recovery restores old or keeps fully committed state, never executes
models/tools. Do not claim hardware power-loss exactly-once.

## Acceptance

- Regression tests prove unavailable history cannot become empty or rotate away.
- Naive timestamp rejection is byte-preserving; offset reset calculations work.
- Both reset kinds preserve preferences/restrictions but not context state;
  failure injection proves pointer and preference files commit together.
- Lifecycle tests exercise repeated cancellation, actual resource settlement and
  error propagation; existing fencing/retired-owner tests stay green.
- Compaction fault tests cover checkpoint and replacement failures/reopen;
  original history is recoverable and no model/tool replay occurs.
- Related Claw/Agent/Skills/TUI tests, Ruff, affected strict typing and workspace
  tests run; limitations and command results recorded in the execution ledger.
