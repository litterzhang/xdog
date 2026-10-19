# Claw Native Receipt Reconnection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans, native continuous execution; no further approval.

**Goal:** Native ACK loss is reconciled against the exact durable input identity instead of restoring already-accepted text as a new request.

**Architecture:** A small application read service exposes the existing Inbox's scoped namespace and one native receipt. The private Gateway adapts bounded read/wait requests without blocking its socket reader; disconnect cancels only the waiter. TUI retains an immutable submission identity/text/images across a lost connection, checks the namespace and looks up the same ID. A missing receipt in the same namespace can resend the original ID; an existing receipt never reruns the Agent. Accepted results are fetched, not rebound to a new execution/TUI.

**Tech Stack:** Python>=3.12/Linux, asyncio, existing InputService/store, private JSON-lines Unix gateway and deterministic-model pytest integration tests.

**Spec:** docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md, sections5.2/5.3/7/11.

## Global Constraints

- Current checkout; no worktree, user-service/data modification, paid providers, push/merge/publish or old-format upgrade.
- No Coding frontend RPC, broker, new Agent/tool loop or approval for message delivery.
- A changed group/workspace/journal namespace is not authority to resend. Preserve original text/images and fail closed.
- A query/wait is read-only; ordinary input execution and Agent steering lanes remain separate.
- Tool permissions and current attachment validation/limits remain unchanged.
- This plan does not complete local pressed-Enter queue persistence, sole-controller cleanup, observer reply capture or Goal/UI.

## Review Focus

1. ACK-loss after commit must not produce a second model turn or quietly generate a new native ID.
2. A wait on an online/pending input must not block ping/abort/control handling, and disconnect must only detach the waiter.
3. Replacement journals/workspaces and conflicting IDs must not convert an old request into a new one.
4. Queued local messages/attachments/composer edits must remain independent from the uncertain active input, not be merged.
5. Stale/malformed receipt responses must not clear another active run or revive consumed Agent steering.

### Task 1: Application receipt reads and thin private Gateway adapter

**Files:** Create `packages/claw/src/xdog/claw/core/runtime/input_queries.py`; modify `core/runtime/gateway.py`; test `packages/claw/tests/test_native_receipts.py`.

**Interfaces:** `NativeInputReceipts(inputs,scope_for_group)` provides `namespace(group)` and `read(group,namespace,message_id,wait=False)`; namespace must match the actual persisted InputScope. Native ID must start `native:` and map to a saved `ReplyTarget('tui',run_id)`. Missing returns None; found returns bounded/redacted id/status/attempt/session/response/error, no original private attachments. No submit/cancel/retry or feature method is exposed.

Private pong negotiates `input_receipts=1` plus `inbox_namespace`; `run_ack` adds that namespace. `input_status` carries request_id/group_id/namespace/message_id/wait(bool), returns `input_status_result` with success/namespace/message-or-null. Each connection owns at most16 read/wait tasks, cancels/joins them on disconnect, and never cancels the actual input producer. The Gateway must not own a new input pump.

- [ ] Write actual Unix socket/model tests: close the first connection after model admission without reading ACK, query running from the second connection, release and read success with exactly one model call. A wait must coexist with ping; disconnect a wait and verify actual receipt/Agent still complete. Missing IDs, replacement namespace, malformed bool/IDs, foreign non-native ID and changed scope fail without execution or mutation.

```python
await send(writer, {'type':'input_status','request_id':'receipt-1','group_id':'main',
                   'namespace':namespace,'message_id':'native:original','wait':True})
await send(writer, {'type':'ping'})
assert (await receive(reader,'pong'))['input_receipts']==1
```

- [ ] Run `uv run pytest packages/claw/tests/test_native_receipts.py -q -n 0`; expected missing capability/handler RED.
- [ ] Implement the read service and bounded per-connection tasks. Gateway is only protocol adaptation; application service validates current scope before/after waits and uses InputService.wait, not a polling Agent loop. Invalid requests produce correlated errors without changing another run.
- [ ] Run native/Gateway/input/ACP tests, Ruff/strict8 mypy and root suite; expected all pass. Commit `feat(claw): expose scoped native input receipts for reconnect`.

### Task 2: Preserve one submission identity across disconnect and reconcile

**Files:** Create `channels/tui/submission.py`; modify `channels/tui/tui_app.py`; tests `test_native_submission.py`, `test_tui_transport.py`, `test_tui_app.py`.

**Interfaces:** Frozen `NativeSubmission` binds group, persisted namespace, native run ID, original text and immutable attachments; receipt negotiation and per-request generation fence remain in TUI. Staged submission states distinguish awaiting ACK/accepted/outcome unknown and terminal. Lookup uses `wait=True`, never a new run ID; known absent in the original namespace can reoffer exactly that original body/ID. A found result is displayed/read from history and removes only that submission. Namespace mismatch/read error preserves it without retry. No per-message or recovery approval is added.

- [ ] Write real ChatApp/queue/response tests: network loss before/after ACK retains one stable native ID/body/images; reconnect emits a read/wait, never an unconditional new message; same namespace+missing receipt resends original ID; existing success/running does not submit; stale response/changed namespace cannot clear or replay; local queued entries and composer edits remain separate. Add an actual Unix transport ACK-loss test using the same ID to prove one model turn.

```python
app._handle_response({'type':'internal_error','message':'socket lost'})
assert list(app._pending_messages)==['queued independently']
assert app._editor.get_text()=='current unsent edit'
# Reconnect reconciles the staged native ID rather than merging all three texts.
```

- [ ] Run targeted tests; expected current `_restore_pending_draft` merges/relabels the active input and drops its stable identity RED.
- [ ] Implement the frozen submission/projection and capability-gated TUI path. Preserve legacy non-negotiated conservative restore behavior only for old peers in the same private transport, not as a data-format migration. Do not reuse ordinary input receipt recovery for Agent steer/follow-up inputs, which retain their existing consumed/returned semantics.
- [ ] Run full Native/TUI/Gateway/input tests and root suite, Ruff/strict8 mypy, Flow examples, lock/diff. Record actual verification/remaining queue-durability limits in `docs/claw-native-reconnect-verification.md`; commit `fix(claw): reconcile native ACK loss without replaying accepted input`.

## Follow-through

Persist pressed-Enter local queue/submission snapshots with original owner/attachments (not per-keystroke composer autosave), then remove duplicate Gateway execution reservations in favor of the sole application controller. Complete managed observer reply capture and optional standalone Goal/two-host/shared UI. No whole-goal completion claim from this plan.
