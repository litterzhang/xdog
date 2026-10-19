# Claw Durable Per-message Output Receipts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans, continuous native execution; no additional approvals.

**Goal:** Channel output transport failures cannot relabel Agent execution, and known-not-sent output can resume without rerunning the Agent.

**Architecture:** An application-owned, separately leased Core output journal captures immutable original input/attempt/destination and each output chunk before sending. A small output delivery service serializes sends per input scope, records send attempts and conservatively marks uncertain sends unknown. Channel adapters resolve original live instances or explicitly restorable Weixin account/sender/context bindings. There is no new Agent loop or protocol completion hook.

**Tech Stack:** Python>=3.12/Linux, asyncio, existing schema-neutral Agent LeasedSqlite helper, SQLite, pytest deterministic models and actual filesystem/process tests.

**Spec:** docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md, sections5.3,7,11.

## Global Constraints

- Current checkout; no worktree, provider fees, user-data/service mutation, push/publish or old-format upgrade.
- Core owns only input/execution/history/general output receipts, not Goal/Eyes business state.
- No automatic replay of an execution or a possibly-sent output; no permission changes or message approvals.
- Persist before transport; cancellation settles admitted disk operations before releasing ownership.
- Per-message output boundaries/order remain intact; never resend the aggregate final body.
- This plan does not close native draft/reconnect, observer reply references or Goal/UI requirements.

## Review Focus

1. Sender acceptance followed by ACK loss/process exit must produce unknown, never an automatic duplicate.
2. A known pre-send rejection can resume the same output IDs without another model turn.
3. A delivery error after progress must not erase history or relabel successful execution failed.
4. Two groups/accounts/connections must not borrow each other's destination or block independent progress.
5. Corrupt/unsupported journal, conflicting duplicate text, lease conflict or shutdown must preserve authoritative facts and fail closed.

### Task 1: Immutable output/send-attempt journal

**Files:** Create `packages/claw/src/xdog/claw/core/outputs/{__init__,store}.py`; test `packages/claw/tests/test_output_store.py`.

**Interfaces:** Frozen `OutputRecord` contains output ID, original InputEnvelope, execution attempt, ordinal, live channel binding, chunk text, delivery status and current send ID. `OutputStore.open(path)` owns `xdog.claw.outputs.v1`; `append(envelope,attempt_id,ordinal,binding,text)` is idempotent/conflict rejecting; `records(scope=None)`, `begin(id)`, `settle(id,send_id,status,error='')`, `close()` are async. Sending status requires a send attempt; restart sending->unknown, pending remains pending; delivered/unknown are never begun again. `settle(...,'pending')` is an explicit proven-not-sent fact, not a generic exception classification.

- [ ] Write real SQLite tests for commit/reopen, conflicts, distinct execution-attempt IDs, send fences/history, pending recovery and sending->unknown. Add an actual child-process exit during sending and lease refusal. Unsupported format/corruption remains byte-unchanged.

```python
row = await store.append(envelope, 'execution-1', 0, 'live-channel', 'progress')
started = await store.begin(row.id)
assert started.status == 'sending'
await store.close()
store = await OutputStore.open(path)
assert (await store.records())[0].status == 'unknown'
```

- [ ] Run `uv run pytest packages/claw/tests/test_output_store.py -q -n 0`; expected missing implementation RED.
- [ ] Implement with LeasedSqlite and schema-owned validation, separate immutable outputs and send attempts. All writes use owned async disk settlement. Do not modify Inbox schema or share feature tables.
- [ ] Run focused tests, Ruff, strict Claw mypy and root tests; expected passed. Commit `feat(claw): persist channel output and send attempt receipts`.

### Task 2: Application delivery and actual channel integration

**Files:** Create `core/outputs/delivery.py`, `core/runtime/channel_adapter.py`; modify `core/runtime/orchestrator.py`, `channels/base.py`, `channels/weixin/api.py` as required; tests `test_output_delivery.py`, `test_weixin_application_intake.py`.

**Interfaces:** `OutputDelivery(store,resolve_sender)` owns per-scope serialization; `emit(envelope,attempt_id,binding,chunks)` persists chunks with independent ordinal IDs, then sends; `resume()` processes only pending outputs; `close()` settles its own sends. `DeliveryNotSent` denotes positive knowledge transport did not accept; other sender failures become unknown. Sender resolution returns None when an original endpoint is absent (keep pending), never substitutes another live TUI. Channel binding/registry lives in the runtime adapter, not the storage/service schema. Actual `_execute_input` wraps trusted channel replies in durable emission, including failure notices; direct SDK display callbacks remain display callbacks.

- [ ] Write actual deterministic-model/application tests: successful execution with channel ConnectionError retains succeeded receipt/history and unknown send; explicit DeliveryNotSent produces pending, fresh application resumes same output ID once with zero model calls; two per-message outputs send once in order and no final aggregate duplicate. Preserve original account/context on restart and never restore another TUI connection. A gated send in one scope cannot block another.

```python
receipt = await service.wait(scope, message_id)
assert receipt.status == 'succeeded'
assert (await outputs.records(scope))[0].status == 'unknown'
assert model.calls == ['original prompt']
```

- [ ] Run `uv run pytest packages/claw/tests/test_output_delivery.py packages/claw/tests/test_weixin_application_intake.py -q -n 0`; expected current channel error marks failed/no durable output RED.
- [ ] Implement the injected sender service/host adapter and wire its initialization before consumption; shutdown signals/stops input producers before closing output storage. Positive API rejections use DeliveryNotSent only where the transport truly rejected before acceptance; malformed/timeout/connection outcomes remain unknown. No detached task or alternate Agent consumer.
- [ ] Run all output/Weixin/input/ACP tests and root suite, Ruff and strict8 mypy; expected all passed. Record commands/counts/limits in `docs/claw-output-receipts-verification.md`. Commit `refactor(claw): decouple execution outcomes from channel delivery` and task-done.

## Follow-through

Continue native draft/ACK-loss reconnect, managed observer immutable reply capture, sole controller/Agent lane cleanup and optional Goal/shared extension UI. Final fresh-context whole-branch review remains a whole-goal gate, not a per-plan subagent review.
