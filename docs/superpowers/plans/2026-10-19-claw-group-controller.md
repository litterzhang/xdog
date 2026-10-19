# Claw Single Group Execution Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans, continuous native execution; no additional approvals.

**Goal:** One application controller owns each group's operation/input execution, including terminal settlement; busy Native intake persists without borrowing another run's controls.

**Architecture:** A per-group controller holds a fenced reservation and actual task identity; reserved command tasks use that same controller and nested same-task acquisitions are reentrant. InputService receives a small execution-scope/admission hook so its existing consumer holds group ownership through durable terminal settlement. Gateway keeps only connection-local views; no global group-running map or legacy collect/steer/backlog queue. Live Native controls bind an actual execution attempt rather than the lifetime of a result waiter.

**Tech Stack:** Python>=3.12/Linux, asyncio, frozen dataclasses, existing InputService/SQLite journal, deterministic model and real Unix socket tests.

**Spec:** docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md, sections5/6/7/11.

## Global Constraints

- Current checkout; no worktree, push/merge/publish, user data/service changes, paid provider or old-format migration.
- Preserve permissions, Agent steer/follow-up semantics, one main Agent and existing per-turn input boundaries.
- No hidden message approval, second consumer/tool loop, broker or fixed child lifecycle budget.
- A reservation is a fenced execution lease, not a protocol source label or model-selected authority.
- Stop signals actual producers; settle admitted disk/cleanup before releasing group ownership.
- Queue capacity/backpressure and background-group admission remain host resource controls, not Agent token/model-call budgets.

## Review Focus

1. A command reserved but not yet admitted must fence concurrent ACP/Native inputs through its actual cleanup.
2. Late/repeated release of an old lease cannot release a successor; pre-start cancellation cannot strand a lease.
3. Queued Native input controls cannot steer/abort another active input; a delayed control cannot target a retry/successor attempt.
4. A group waiting for another group's background capacity cannot block unrelated human input; cancellation preserves ownership until settlement.
5. Capability withdrawal/unstarted cancellation must publish exactly one original receipt while the socket reader remains usable.

### Task 1: Fenced per-group controller

**Files:** Create `packages/claw/src/xdog/claw/core/runtime/group_controller.py`; test `packages/claw/tests/test_group_controller.py`.

**Interfaces:** Frozen `GroupReservation(group_id,token)`; `GroupController(group_id,background_slots)` implements `reserve()->GroupReservation|None`, `release(reservation)->bool`, `acquire(is_user=False,reservation=None,input_key=None)`, `start(reservation,coroutine)->Task`, `confirm_input(message_id,attempt_id,session_id)`, `owns_input(message_id,attempt_id=None)`, `cancel_input(message_id,attempt_id)` and `busy`. An input_key is (message_id,attempt_id). Only its actual current task can confirm admission. Same-task nested acquire does not release the outer lease; another task cannot borrow it. start binds an already-fenced reservation and releases on pre-start cancellation or settled coroutine exit; repeated/stale release is harmless to successors.

- [ ] Write real asyncio tests for reserved-before-start exclusion, cancellation before first step, same-task nesting, rejecting a different group's lease, stale release after a successor, actual task-confirmed input controls, delayed cancellation cleanup, background capacity and independent human groups.

```python
lease = controller.reserve()
started = asyncio.Event()
async def operation():
    async with controller.acquire(is_user=True):
        started.set()
job = controller.start(lease, operation())
await started.wait()
await job
assert not controller.busy
```

- [ ] Run `uv run pytest packages/claw/tests/test_group_controller.py -q -n 0`; expected missing module RED.
- [ ] Implement small frozen owner state plus per-group availability signal and injected shared background semaphore. No message collection, durable queue, Goal/session methods or protocol knowledge.
- [ ] Run focused tests, Ruff/strict Claw mypy and root suite; expected green. Commit `feat(claw): add fenced per-group execution controller`.

### Task 2: Hold original consumer/host operations through settlement

**Files:** Modify `core/inputs/consumer.py`, `core/runtime/orchestrator.py`, `core/runtime/gateway.py`; remove `core/queue.py` after all consumers switch; update concurrency tests and obsolete mixed-mode-only tests; add `test_group_execution_integration.py`.

**Interfaces:** InputService accepts optional execution_scope(record) async context manager and on_admitted(record,session_id) callback; the original consumer keeps its owner until terminal/interrupt settlement. Orchestrator owns a GroupController per group and shared background slots, exposes fenced reservation/start-operation calls. Existing command service methods nest in the same operation task. Gateway starts reserved command jobs through the application, releases only the captured lease, removes global _group_runs/_group_runs_lock and leaves connection-local run views intact. Legacy MessageQueue collection/mixed-lane execution is removed, not wrapped with a compatibility facade.

- [ ] Write actual InputService/Orchestrator tests: hold a reserved operation before it starts while ACP-like input is committed; no model admission until release, then exactly once. Gate terminal/interrupt disk settlement and verify another group operation cannot enter. Preserve existing command cancellation/withdrawal, permissions/navigation and concurrency contracts, including the no-model argument-completion tests.

```python
operation = app.reserve_group('main')
await app.route_message(input, wait_for_result=False)
assert model.calls == []
app.release_group(operation)
assert (await inputs.wait(scope, identifier)).status == 'succeeded'
```

- [ ] Run the new tests; expected current acquire ignores reservations/releases before Core settlement RED.
- [ ] Wire the consumer scope/admission hooks; use controller-owned command task factories, not context-variable authority or a second operation queue. Preserve original guarded receipt publication before successor admission. Remove dead mixed queue policy and tokenless releases. Keep active ordinary Native reservation temporarily captured as an explicit lease until Task3 changes that ingress.
- [ ] Run all Claw and root tests, Ruff/strict8 mypy; expected green, without dropping safety/permission tests. Commit `refactor(claw): centralize group ownership through durable settlement`.

### Task 3: Durable busy Native intake and exact-attempt controls

**Files:** Modify `gateway.py`, `orchestrator.py`, `input_queries.py` as needed; test `test_native_busy_intake.py` and existing Gateway/TUI/input tests.

**Interfaces:** Ordinary Native message intake no longer reserves/rejects merely because its group is busy; it uses the same durable Core consumer. A connection-local execution future is completed only by actual original admission with attempt/session identity. Input controls and abort compare that exact identity with the controller. Queued cancellation uses explicit Core withdrawal; admission races cannot cancel another input. Caller disconnect only detaches display; no original lease is released by a result-waiter callback.

- [ ] Write real multi-client socket tests: one held model, second Native input ACKs pending while model count stays1; second client's steer/abort cannot touch first; queued cancellation stays unstarted/cancelled; first completes then remaining inputs run individually. Gate a stale control past another input's admission and prove no successor cancellation. Receiver disconnect/restart preserves the accepted queue.

```python
await send(second, {'type':'message','run_id':'queued','group_id':'main','content':'second'})
await receive(second_reader,'run_ack')
assert (await inputs.store.get(scope,'native:queued')).status in {'pending','claimed'}
assert model.calls == ['first']
```

- [ ] Run targeted Native/Gateway tests; expected busy rejection/no original-attempt binding RED.
- [ ] Implement acceptance/admission separation and preserve ordinary versus Agent lanes; no message approval or group abort based on another waiter's lifetime. Do not introduce a second frontend execution journal merely to duplicate Core intake. Keep unsent editable UI queue as explicit unsent state; separately document its process-memory boundary.
- [ ] Run full8 suite, focused real protocol/permissions tests, Ruff/strict8 mypy, Flow examples, lock/diff. Document evidence/remaining Goal+observer work in `docs/claw-group-controller-verification.md`; commit `feat(claw): accept queued native input without borrowing active controls`.

## Follow-through

Complete observer-created immutable reply references, then standalone optional Goal with both real host adapters and shared extension UI. Existing legacy Goal implementation remains transitional until its explicit cutover; final whole-branch review and nine-package/installed Goal verification remain mandatory. No whole-goal completion from this plan.
