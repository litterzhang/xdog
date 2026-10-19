# Claw Durable Channel Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Native continuous execution is authorized; no additional user approval.

**Goal:** Commit Weixin intake before its upstream cursor and send each input's replies to its captured sender/context, preserving the single application consumer.

**Architecture:** The poller invokes a lightweight durable-accept callback before enqueuing presentation work or advancing the cursor. Channel dispatch may wait for results but cannot own execution. Application route_message accepts without waiting when requested, using the same stable ID for later dispatch/recovery. Reply operations receive the immutable original UserInput rather than a mutable latest-recipient map.

**Tech Stack:** Python>=3.12/Linux, asyncio, existing Core InputStore/InputService, Weixin monitor/API adapters and pytest real-IO deterministic-model tests.

**Spec:** docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md

## Global Constraints

- Current checkout/branch; no worktree, provider fees, user-service/data mutation, push or publish.
- No new Agent/tool loop, message approval or automatic side-effect replay.
- Keep steering as Agent semantics; accepted channel inputs use the sole durable consumer.
- Stable upstream identity is mandatory for durable intake. Missing identity refuses cursor ACK, never guesses a random ID or silently merges distinct requests.
- Captured destination/context is host routing, not permission inferred from source labels.
- This plan is channel handoff, not the optional Goal/shared UI deliverable or final whole-goal completion.

## Review Focus

1. Partial batch acceptance and ACK/cursor-save loss must reoffer original stable IDs without model replay.
2. A receiver stop after acceptance must not erase durable inputs or cancel another input's execution.
3. Two peers sharing a group must not redirect each other's pending/progress/final replies or typing ownership.
4. Duplicate input and changed routing/content must not overwrite the original reply binding.
5. Missing upstream identity, full core capacity or actual disk failure must block cursor advancement without dropping accepted inputs.

### Task 1: Poller commit-before-cursor contract

**Files:**
- Modify `packages/claw/src/xdog/claw/channels/weixin/monitor.py`.
- Test `packages/claw/tests/test_weixin_durable_poll.py`.

**Interfaces:**
- `MonitorOpts.on_accept: Callable[[WeixinMessage],Awaitable[None]] | None = None`.
- When present, await actual acceptance for each supported user message before FIFO dispatch and cursor save. Exception leaves the old cursor; cancellation never advances a partially accepted batch.
- Existing on_message is presentation/result dispatch, not proof of durable acceptance.

- [ ] Write controlled actual _poll_updates tests holding acceptance at a barrier, partial failure/reoffer and cancellation. Assert the sync file's actual bytes/cursor, not mocked call order.

```python
await accepted.wait()
assert not sync_file.exists()
release.set()
assert json.loads(sync_file.read_text())['get_updates_buf']=='next'
```

- [ ] Run `uv run pytest packages/claw/tests/test_weixin_durable_poll.py -q -n 0`.
  Expected: missing callback/current cursor advances before the commit barrier.
- [ ] Implement optional durable intake callback, retaining backpressure and no cursor past partial intake. Run monitor tests and root suite; Ruff/strict Claw mypy. Expected: passed, no new skips.
- [ ] Commit `feat(claw): fence Weixin cursor on durable acceptance`; task-done records monitor/handoff tests.

### Task 2: Actual channel/application intake and original-recipient replies

**Files:**
- Modify `channels/base.py`, `channels/weixin/channel.py`, `core/runtime/orchestrator.py`.
- Test `test_weixin_application_intake.py`, update existing channel tests without weakening ordering/typing assertions.

**Interfaces:**
- `Channel.set_on_accept(callback)` registers the application's lightweight durable intake.
- `Channel.send_reply(original: UserInput,text: str)` projects a captured destination; Weixin override uses original sender/account/context, not latest map.
- `route_message(..., wait_for_result: bool=True)` can return durable queued intake without waiting; existing reserved result wait stays unchanged.
- Stable Weixin message key derives from explicit upstream message_id or client_id and account/sender namespace. Replay duplicates use exactly that ID; no timestamp-now fallback in immutable envelope.

- [ ] Write real Orchestrator/CoreStore tests with two senders in one group, deterministic gated model and actual send-request data. Prove cursor acceptance is lightweight, records survive receiver absence, duplicate IDs do not rerun, changed body conflicts, missing IDs fail closed.
- [ ] Run targeted new tests. Expected: current runtime uses mutable recipient and no durable poll callback.
- [ ] Implement pure inbound projection and accept/dispatch split, captured reply send, result wait reusing the original ID. Keep legacy callback-only channel usage separate from actual application durable binding; do not implement an alternate consumer/store.
- [ ] Run all Weixin/input/ACP and root tests; Ruff/strict8 mypy. Expected: all pass, no weakened safety assertion. Commit `refactor(claw): bind channel replies to original durable input`.

## Follow-through

Native pre-acceptance queued drafts/reconnect, durable output-send receipts/recovery and observer-created callback reply references still need their final application interfaces. Do not claim IN-06/IN-08 or full channel recovery merely from captured incoming reply metadata. Continue optional standalone Goal/Coding adapter and shared extension UI after the intake boundary, then finish remaining whole-objective gates.
