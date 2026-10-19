# Claw ACP and Observer Ownership Separation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Autonomous Native execution is authorized; do not ask for another approval.

**Goal:** Retire ACP's duplicate receipt journal/consumer and move observer cursor/outbox/report storage into the observer capability, leaving application input receipts authoritative.

**Architecture:** ACP addresses groups and forwards canonical external intake/status/wait to the existing application service. Core storage supplies stable per-scope namespaces and bounded read-only status pages. Observers persist their own immutable outbox and read core receipt/attempt facts through APIs; they never write core cancellation based on observer/Goal status. Agent explicitly stops collection, notifications or inputs as distinct actions.

**Tech Stack:** Python>=3.12/Linux, asyncio, stdlib sqlite3/private leases, existing official ACP SDK and Eyes sources/router/handler.

**Spec:** docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md

## Global Constraints

- Current checkout/branch only; no worktree, push, publish, user services/data, paid model or new tool loop.
- No old-format migration/dual-read. Existing unsupported files must remain byte-identical after refusal.
- Core input receipts are sole execution facts; observer execution projections are not another authority.
- Cancellation of observation does not withdraw already accepted input or cancel worker execution/report delivery.
- Source/provenance labels do not grant routing/control authority; own-journal associations and actual namespaces do.
- No Goal logic/schema/consumer/report policy in ACP. Any temporary old Goal notification projection is explicitly isolated in its capability and removed during Goal extraction.
- Keep all47 objective requirements and final nine-package/Goal/UI/review gates pending until actually proven.

## Review Focus

1. ACP/observer restart must keep the same core namespace and never mislabel continued work interrupted.
2. Source cancel racing enqueue ACK must not retract accepted input; explicit core withdrawal still fences admission.
3. Claimed/running/terminal attempts and their report send outcomes must not be confused across explicit retries.
4. Callback/transport startup or shutdown IO must settle actual owners; failed initialization cannot leave half-activated scopes.
5. Status/query and display operations must not create a model turn, Goal work or new observer authority.

### Task 1: Core namespace/status and owned startup/shutdown boundaries

**Files:**
- Modify `packages/claw/src/xdog/claw/core/inputs/store.py`.
- Modify `packages/claw/src/xdog/claw/core/inputs/consumer.py`.
- Modify `packages/claw/src/xdog/claw/core/runtime/orchestrator.py` and `gateway.py`.
- Test `test_input_scope_status.py`, `test_input_start_stop.py`.

**Interfaces:**
- `InputStore.bind_scope(scope)->str` explicitly registers a trusted scope namespace.
- `InputStore.namespace(scope)->str` is read-only and refuses unbound scope.
- `InputStore.status(scope, selected=None, cursor=0, limit=64)->(list[dict[str,object]], int|None)`.
- `InputService.request_stop()->None` synchronously closes admission/signals actual consumers; async close joins them and only then closes storage.
- Orchestrator initialization is one owned task, finishing all activation or closing failed resources before publishing its service.

- [x] Write real-store tests: namespace stable through reopen; same label/different workspace isolated; status/cursor/selected validation; no SQL writes/Agent construction during inspection; unsupported previous schema refused unchanged.

```python
namespace = await store.bind_scope(scope)
await store.enqueue(envelope)
rows, cursor = await store.status(scope, limit=1)
assert rows[0]['message_id'] == 'one'
assert await store.namespace(scope) == namespace
```

- [x] Run `uv run pytest packages/claw/tests/test_input_scope_status.py packages/claw/tests/test_input_start_stop.py -q -n 0`.
  Expected: missing APIs then startup/early-stop boundary failures.
- [x] Implement new unreleased schema with scopes, no ALTER/upgrade branch; validate scopes before recovery. Add explicit early stop, preserve admitted disk work and full producer settlement.
- [x] Run all input tests, gateway shutdown and complete root suite; Ruff/strict8 mypy.
  Expected: pass; no new skips. Commit `feat(claw): expose authoritative input scope receipts`; task-done records input/start-stop tests.

### Task 2: Independently leased observer journal

**Files:**
- Create `packages/claw/src/xdog/claw/core/observers/journal.py` for private off-loop journal ownership.
- Replace `core/observers/store.py` implementation; no import/use of ACP inbox.
- Test `test_observer_journal.py`; preserve source/outbox identity and fault tests when moving old fixtures.

**Interfaces:**
- `ObserverStore.open(path, inputs: InputService)->ObserverStore`, `close()` owns only its journal.
- Existing create/get/list/save/message/history/fail/complete semantics remain scoped.
- `cancel` stops future collection/unsent outbox only; `cancel_notifications` separately stops known-not-sent reports.
- `sync` reads core receipt/attempt facts and records projection/report identity. No raw SQL joins or writes to core inputs.
- `begin_delivery` / `finish_delivery` fence actual report attempt; sending recovery becomes unknown, never blind retry.

- [ ] Write actual two-store tests: cursor+whole outbox atomic rollback; dedup immutable body; unsupported files/lease conflicts; stable namespace recovery; independent close; observer cancel leaves accepted core input pending/running; explicit core withdrawal still works.

```python
await observers.save(record, {'cursor': 1}, (canonical,))
await inputs.submit(envelope)
await observers.cancel(scope.group_id, scope.workspace, record.id)
assert (await inputs.store.get(scope, canonical.id)).status != 'cancelled'
```

- [ ] Run `uv run pytest packages/claw/tests/test_observer_journal.py -q -n 0`.
  Expected: old constructor/storage coupling and automatic accepted-input withdrawal do not meet contract.
- [ ] Implement separate versioned private sqlite journal and receipt API reads. Keep actual attempt binding separate from per-occurrence message ID and report outcomes. No periodic model work.
- [ ] Run ownership/fault/observer journal + core input tests and root suite. Expected: pass, no duplicated input authority or changed tool policy. Commit `refactor(claw): give managed observers their own journal`.

### Task 3: Switch actual ACP/managed runtime and delete old bridge

**Files:**
- Replace `acp/backend.py` with addressing/intake/status/wait/peer cancellation only.
- Modify `acp/server.py`, `core/observers/manager.py`, `core/runtime/orchestrator.py`, `gateway.py`.
- Isolate temporary old Goal hint projection in `core/planning/notification_adapter.py`, never ACP/core consumer.
- Delete `acp/inbox.py`; update test fixtures to use core service/observer journal rather than retain compatibility aliases.
- Update ACP/Eyes/process/display/Goal recovery tests, docs/AGENTS/artifact probes.

**Interfaces:**
- Backend owns peer bindings/prompt waiters, not store/consumer/report tasks; close cannot close application.
- Manager accepts application facade/service, not ClawBackend. Source timing/collection still uses unmodified Eyes implementations and generic ACP handler.
- Application offers scoped settled hints and capability-owned optional input projection. Neither hook chooses Goal actions or cancels work by Goal/observer status.
- Gateway display listens to application receipts, not adapter-owned completion callbacks.

- [ ] Add true official-SDK Unix tests: pending/running survives endpoint detach, result/status read same namespace/record as native intake, no second model call, generic status stays model-free.
- [ ] Add real scheduler/file -> generic ACP -> core input -> Agent -> captured report tests, ACK-loss recovery and explicit separate cancel operations.
- [ ] Run targeted commands before cutover; expected RED for duplicate/mislabeled old ACP receipt and adapter lifecycle ownership.
- [ ] Switch consumers, remove old journal/module, update old cancel tests to stronger new contracts rather than disable tests. Keep process/crash/namespace/unknown-send coverage.
- [ ] Run full root, strict8 mypy/Ruff/Flow/lock and fresh wheels. Expected: all pass; scans show no ACP Goal/observer/schema/consumer references. Commit `refactor(claw): make ACP a thin application adapter` and record remaining whole-goal work explicitly.
