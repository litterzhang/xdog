# Claw Durable Input Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish protocol-independent durable input data and receipt storage as the first dependency of the complete Claw refactor; do not claim channel cutover or the overall goal complete.

**Architecture:** Core input storage knows only canonical submitted content, immutable trusted routing and attempt facts. It has no ACP, Eyes or Goal lifecycle imports. Consumers/channels will switch to this store in the following plan; do not start a second active consumer during this foundation stage.

**Tech Stack:** Python >=3.12, frozen dataclasses, stdlib sqlite3, existing xdog.agent canonical input and owned-work settlement, pytest-asyncio.

**Spec:** docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md

## Global Constraints

- Current checkout and `refactor/claw-assistant-core`; no worktree, service restart, push, publish or user-data mutation.
- Python >=3.12, Linux; no extra dependency, broker or model/tool loop.
- No migration/legacy dual-read; unsupported database format must fail without modifying original data.
- Messages/receipts/history are core-owned; Goal and observer business tables are not core input schema.
- Source/provenance are content, never authority. Readonly/tool permissions remain unchanged.
- No automatic replay of running attempts; claimed-but-unstarted work may safely return to pending.
- This stage must not alter existing runtime behavior until a single-owner cutover is implemented.

## Review Focus

1. Same ID and changed recipient, owner, attachment or submitted content must not alias a previous ACK.
2. Cancellation while sqlite fsync/commit is admitted must settle real work before lease release; disk errors must not be hidden by cancellation.
3. A stale attempt cannot start/finish/cancel a newer attempt, including after storage reopen.
4. Unknown/legacy/corrupt/symlink or unowned store must fail closed without initialization overwriting it.
5. Inbox inspection and payload freezing must not expose one owner's input or allow caller mutation to alter accepted content.

## File structure

Create `packages/claw/src/xdog/claw/core/inputs/`:
- `models.py`: immutable Scope/ReplyTarget/Envelope and JSON codecs. No runtime objects.
- `store.py`: single-owner schema/transactions and receipts, all disk work off loop.
- `__init__.py`: documentation only; no service boot or registration.

Tests: `test_input_envelope.py`, `test_input_store.py`, `test_input_store_ownership.py` under Claw tests.
Existing ACP runtime remains untouched during this plan. Later consumer integration removes its parallel store implementation.

### Task 1: Immutable submitted content and trusted routing envelope

**Files:**
- Create: `packages/claw/src/xdog/claw/core/inputs/__init__.py`
- Create: `packages/claw/src/xdog/claw/core/inputs/models.py`
- Test: `packages/claw/tests/test_input_envelope.py`

**Interfaces:**
- Consumes: existing `SessionInput`, `decode_input`, `input_data` and `ImageContent`.
- Produces: frozen `InputScope(group_id: str, workspace: Path)`, `ReplyTarget(channel: str, recipient: str)`,
  `InputEnvelope(scope, message, reply=None, images=(), human_authorized=False, admission_key='')`,
  `encode_envelope(value: InputEnvelope) -> str`, `decode_envelope(payload: str) -> InputEnvelope`.
- Encoding severs mutable evidence/provenance, enforces strict key sets, 1MiB canonical content budget and explicit separate image/total envelope bounds. Routing is immutable host-supplied data, not inferred from source.

- [ ] Step 1: Write a real encode/decode round-trip containing one independent human message and image; change original provenance and prove persisted decoded payload is unchanged. Add invalid/unknown fields, invalid bool, NUL labels, relative scope, unsupported image MIME and oversized payload cases.

```python
value = InputEnvelope(InputScope('main', tmp_path), message,
                      ReplyTarget('weixin', 'user-123'), human_authorized=True)
payload = encode_envelope(value)
message.provenance['source'] = 'forged'
assert decode_envelope(payload).message.provenance == {'source': 'original'}
```

- [ ] Step 2: Run `uv run pytest packages/claw/tests/test_input_envelope.py -q -n 0`.
  Expected: fails because the new input envelope module does not exist.
- [ ] Step 3: Implement validation and canonical JSON. Do not resolve symlinks into a different implicit workspace identity; reject nonabsolute/traversal scope. Serialize no callables, TUI handles or sessions.
- [ ] Step 4: Rerun the command. Expected: all envelope tests pass; valid canonical identity and attachment order survive unchanged.
- [ ] Step 5: `uv run ruff check packages/claw/src/xdog/claw/core/inputs packages/claw/tests/test_input_envelope.py` and strict Claw mypy. Expected: passed.
- [ ] Step 6: Commit only Task 1 files with `feat(claw): define protocol-neutral input envelopes` and record task-done with the test command above.

### Task 2: Durable intake, attempt identities and explicit state transitions

**Files:**
- Create: `packages/claw/src/xdog/claw/core/inputs/store.py`
- Test: `packages/claw/tests/test_input_store.py`

**Interfaces:**
- Consumes Task 1 codecs and `xdog.agent.async_work.wait_owned`.
- Produces `InputRecord(envelope, status, attempt_id='', response='', error='', session_id='')` and `InputStore.open(path, max_pending=50)`.
- Async store methods: `enqueue(envelope) -> tuple[InputRecord, bool]`, `get(scope, message_id) -> InputRecord | None`,
  `records(scope) -> tuple[InputRecord,...]`, `claim(scope) -> InputRecord | None`,
  `start(record) -> InputRecord`, `finish(record, status, response='', error='', session_id='') -> InputRecord`,
  `cancel(scope, message_id) -> InputRecord`, `retry(scope, message_id) -> InputRecord`, `close() -> None`.
- `claim` only pending -> claimed; `start` matching attempt claimed -> running; `finish` only matching running -> succeeded/failed/interrupted.
- Cancel is explicit and idempotent for pending/claimed; running cancellation is an execution-owner operation, not a store lie.
- Retry only failed/interrupted, preserving attempt history; duplicate enqueue never retries.

- [ ] Step 1: Write tests using real private tmp directories/database, not mocked SQL. Catch loss of pending messages, ID collisions with changed routing/content/images, scope isolation and full capacity behavior.

```python
first, duplicate = await store.enqueue(envelope)
assert first.status == 'pending' and not duplicate
claimed = await store.claim(envelope.scope)
assert claimed.status == 'claimed' and claimed.attempt_id
running = await store.start(claimed)
assert running.status == 'running'
finished = await store.finish(running, 'succeeded', response='done')
assert finished.response == 'done'
```

- [ ] Step 2: Run `uv run pytest packages/claw/tests/test_input_store.py -q -n 0`.
  Expected: fails because InputStore is missing.
- [ ] Step 3: Implement private owned lease, fixed new format, sqlite transactions serialized off-loop, immutable payload de-duplication and transition fences. Store current message state and append attempt records separately; no business tables or implicit retries.
- [ ] Step 4: Add close/reopen tests: pending and claimed recover; running becomes interrupted; old claim cannot start a new attempt; inspected terminal input still returns the original envelope and reply target.
- [ ] Step 5: Run `uv run pytest packages/claw/tests/test_input_envelope.py packages/claw/tests/test_input_store.py -q -n 0`.
  Expected: all pass. Run Ruff and strict Claw mypy. Expected: passed.
- [ ] Step 6: Commit Task 2 files with `feat(claw): persist core input and execution receipts`; task-done uses the combined test command.

### Task 3: Process/IO ownership and cancellation fault regressions

**Files:**
- Modify: `packages/claw/src/xdog/claw/core/inputs/store.py`
- Test: `packages/claw/tests/test_input_store_ownership.py`

**Interfaces:**
- Consumes actual InputStore methods and private directory contract from Task 2; no new runtime consumer.
- Produces proven single-owner lifecycle and unchanged original files on unsupported schema/corruption.

- [ ] Step 1: Add a second-process lease test; unsupported format/symlink tests compare original file bytes; prove a cancelled writer cannot release owner before its real transaction settles.

```python
await store.close()
reopened = await InputStore.open(path)
try:
    assert (await reopened.get(scope, 'pending-id')).status == 'pending'
finally:
    await reopened.close()
```

- [ ] Step 2: Run `uv run pytest packages/claw/tests/test_input_store_ownership.py -q -n 0`.
  Expected: identify an unprotected ownership/fault condition; if a case passes, document it as characterization rather than claiming RED for existing behavior.
- [ ] Step 3: Fix reproduced gaps with the smallest implementation changes, preserving transaction error precedence over observer cancellation. Add stale attempt, concurrent enqueue/fullness, multiple cancellation and invalid max_pending cases.
- [ ] Step 4: Run `uv run pytest packages/claw/tests/test_input_envelope.py packages/claw/tests/test_input_store.py packages/claw/tests/test_input_store_ownership.py -q -n 0`.
  Expected: all pass. Run complete `uv run python scripts/test.py`, Ruff packages and strict8 namespace mypy. Expected: no new failures/skips; disclose any failures by name.
- [ ] Step 5: Commit with `test(claw): fence durable input ownership and recovery`; task-done runs the combined foundation tests. Record that IN-01/IN-07/RUN-03 remain pending until real channels and consumers switch.

## Following plans and completion boundary

After this plan, continue without another approval: implement single application consumer, thin ACP and durable TUI/Weixin handoff; isolate managed Eyes; extract Goal and two host adapters; deliver generic read-only extension views and one Goal UI extension; remove old active implementations; run full9-package and fresh-artifact verification. The 47-item objective remains active throughout. This plan's completion is only the input foundation, not feature delivery or final review of the full refactor.
