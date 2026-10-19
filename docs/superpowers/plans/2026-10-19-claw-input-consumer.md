# Claw Application Input Consumer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Native autonomous execution is already authorized; no further approval gate.

**Goal:** Make the durable input foundation execute real Claw turns through one application-owned consumer, then route live Orchestrator inputs through it without implicit steering or accepted-message loss.

**Architecture:** InputService holds the existing InputStore and one controller task per scope. An injected application executor owns the existing group exclusion and calls an explicit `admit(session_id)` boundary before Agent execution. No new model/tool loop. Protocol and Goal-specific policy stay outside the consumer; callbacks are process-local presentation/admission bindings, not persisted business objects.

**Tech Stack:** Python >=3.12/Linux, asyncio, existing InputStore and Agent runtime, sqlite, pytest.

**Spec:** docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md

## Global Constraints

- Current checkout/feature branch, no worktree, push, publish, model fees, user files or live services.
- No changed tool authorization, message approval, hosted broker or second tool loop.
- Steer/follow-up belong to Agent; ordinary queued input never becomes steering.
- Keep whole47-item objective; intermediate ACP receipt storage is explicitly retired in the subsequent protocol/observer cutover, not treated as final architecture.
- Native implementation, local commits, RED/GREEN and real IO/process evidence.

## Review Focus

1. Lost ACK or cancelled wait must not strand a committed pending message or cancel another message's execution.
2. A selected-but-unstarted cancellation must win admission; shutdown after actual terminal commit must not overwrite success.
3. Distinct queued inputs retain their own result/recipient, never borrow a later callback or infer authority from metadata.
4. Corrupt/mismatched data or an unknown admission binding must fail closed; callback failure cannot replay Agent execution.
5. Ordinary queue mode no longer silently steers; explicit Agent inputs and existing Coding batch semantics remain intact.

### Task 1: Protocol-independent application consumer

**Files:**
- Create `packages/claw/src/xdog/claw/core/inputs/consumer.py`.
- Modify `packages/claw/src/xdog/claw/core/inputs/store.py` to expose explicit safe `release(record)` for unstarted cleanup only.
- Test `packages/claw/tests/test_input_consumer.py`.

**Interfaces:**
- Consumes `InputStore`, `InputEnvelope`, `InputRecord` from foundation.
- Produces frozen `InputOutcome(response: str='', error: str='', session_id: str='')` and
  `InputService(store, execute)` where `execute(record, admit) -> Awaitable[InputOutcome]`.
- `admit(session_id: str='') -> Awaitable[bool]` commits running only if original claim is still current.
- `submit(envelope) -> tuple[InputRecord,bool]`, `wait(scope,id) -> InputRecord`, `wake(scope)`, `close()`.
- A cancelled `wait` detaches, not withdraws; explicit `store.cancel` makes admission return false.
- Executions must call admit. An executor that returns without admission cannot record success.

- [ ] Write tests using real InputStore and an actual `xdog.agent.Agent` with deterministic `EventStream.empty` model; verify two inputs and each result/history, duplicate ACK, wait cancellation, pending withdrawal, failed exception, close during pre-admission/running/after terminal commit.

```python
async def execute(record, admit):
    if not await admit('context-1'):
        return InputOutcome()
    stream = await agent.prompt(record.envelope.message.instruction)
    async for event in stream:
        pass
    return InputOutcome(response=agent.messages[-1].content[0].text)
```

- [ ] Run `uv run pytest packages/claw/tests/test_input_consumer.py -q -n 0`.
  Expected: missing consumer module, then named behavioral failures rather than a false successful store stub.
- [ ] Implement one consumer task per scope, stable wake signals, waiter notification after durable terminal receipt, cancellation-safe claim/result cleanup and explicit scope wake on startup. No event polling for model work.
- [ ] Run consumer + foundation tests and full root suite; Ruff/strict Claw mypy.
  Expected: pass, no new skips. Commit `feat(claw): consume durable inputs in application service`; task-done records targeted combined command.

### Task 2: Durable runtime projection and real Orchestrator cutover

**Files:**
- Modify `core/inputs/models.py`: bounded immutable host context, separate from message provenance.
- Create `core/runtime/input_adapter.py`: freeze/restore UserInput/ExternalInput/SystemInput/ContextContinuation; host metadata cannot be supplied through ACP evidence.
- Modify `core/runtime/orchestrator.py`: open/own InputStore/service, route all new messages through submit, execute with existing group guard and explicit admission, settle service before shared resources.
- Modify `core/runtime/gateway.py`: run ACK after durable intake (via accepted callback), not before route enters storage.
- Test `test_input_runtime.py`, update old implicit-queue tests to assert independent queued turns, retaining explicit steering tests.

**Interfaces:**
- Consumes Task1 service and current `_execute_with_queue` guard; no second execution lock.
- `InputEnvelope(..., context: dict[str, JsonValue] | None=None)` owns frozen host context and codec budget.
- `route_message(..., message_id: str | None=None, on_accepted: Callable[[],None] | None=None)` retains existing result/presentation callbacks.
- Busy nonreserved callers receive queued None after durable ACK; reserved callers wait for their own result, preserving old ACP/Goal waiter contracts until the next cutover.
- User origins/attachments/kind and exact ContextContinuation remain distinct. Unknown restored process-local admission keys cannot execute.

- [ ] Write deterministic real-Orchestrator tests: no ACP socket, ordinary busy messages persist and do not steer, results independent, limit rejects without loss, recovered pending runs once, closed receiver cannot close application service.
- [ ] Run `uv run pytest packages/claw/tests/test_input_runtime.py -q -n 0`.
  Expected: inputs not persisted / implicit steering observed before cutover.
- [ ] Implement codec/projection then consumer integration; no change to model loop, permissions or Coding's controller. Stop old collect/debounce draining in route_message. Keep full live TurnResult and receipts consistent.
- [ ] Run affected gateway/ACP/Eyes/Goal/Weixin/input tests and full root suite; fix behavioral regressions instead of skipping/weaking tests. Source coupling scans identify temporary old ACP storage for immediate next plan.
- [ ] Commit `refactor(claw): route ordinary turns through durable application intake`; task-done records real runtime tests and foundation/consumer suite.

## Follow-through

After runtime cutover, continue thin ACP/observer storage separation, native/channel admission and fixed reply routing, extract standalone Goal and both host adapters, shared extension views/UI, delete old active paths and finish all47 acceptance items. This plan alone cannot pass BOUND-01 or claim full channel durability while old monitor/client admission remains.
