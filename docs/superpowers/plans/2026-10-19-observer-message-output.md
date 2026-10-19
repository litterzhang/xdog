# Observer per-message output and explicit notification cancellation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans inline with TDD/ledger. The user authorized implementation without further approvals. One fresh-context whole-branch review follows the complete objective, not each phase.

**Goal:** Make managed observer-triggered Agent responses use Core's existing per-assistant-message durable output path, with immutable original destinations and independently explicit notification cancellation.

**Architecture:** A small generic host output-source registration resolves a frozen original reply from precommitted feature intent before external intake ACK. Core persists that routing in the immutable input envelope, emits each actual assistant message through OutputDelivery, and owns send/cancel facts. ManagedObservers provides only canonical outbox/ref verification and explicit notification policy, then mirrors Core delivery facts; ACP and GroupController contain no feature decision or report pump.

**Tech Stack:** Python3.12 async/frozen models, existing canonical SessionInput/InputEnvelope, Core InputService/OutputDelivery/OutputStore, ReplyReference, managed ObserverJournal and actual private Unix ACP.

**Spec:** `docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md`, sections5,7,8,11; IN-06/08, observer cancellation/ownership and no-replay acceptance requirements.

## Global Constraints

- Current checkout/local feature branch only; no worktree/user service/data/cache deletion/paid model/push/merge/publish/version bump.
- All ordinary messages remain durable before ACK, accepted messages are not deleted, source labels/provenance never select recipients or permissions. No message approval/human wake-up quota.
- Eyes event delivery still uses its registered generic ACP handler. Do not shortcut model callbacks through CLI, a harness-specific Eyes handler or a direct private model loop.
- Exactly one main execution owner/controller/Agent loop per group. Core handles generic input/output facts; feature owns source/notification intent. No Goal/Eyes checks or business tables in ACP/Controller.
- Source stop, notifications stop, pending-input withdrawal and worker cancellation are distinct. Already entered sends/effects cannot be undone. Unknown send/attempt is not replayed.
- No old-format migration/dual-read facade; new output/observer formats refuse old bytes before recovery, preserve them and document explicit fresh-root choice. Core input canonical payload/image/context capacities must not expand.
- Use Bash with `TMPDIR=/data/t.Vy5SaQ UV_CACHE_DIR=/data/t.Vy5SaQ/uv-cache TMUX_TMPDIR=/data/t.Vy5SaQ`; root is full. Keep all tests/physical terminal/sockets/deadlines intact.

## Current source evidence / decisions

- `ManagedObservers._report` currently sends aggregate `receipt.response` through `Orchestrator.deliver_report`; this must be removed, not kept beside streaming.
- `Orchestrator._execute_input` already builds `durable_reply` for ChannelReply or recovered ordinary UserInput, excluding ExternalInput. AgentSession already emits each complete assistant message. Reuse that path.
- `OutputStore` currently `xdog.claw.outputs.v1`; sending/unknown/pending are separate from execution. Add explicit unsent cancellation as formatv2, never rewrite actual send attempts as unsent.
- Core `InputStore` is `xdog.claw.inbox.v3`; `InputEnvelope` payload `xdog.claw.input.v2` has frozen host context. Preserve canonical message/attachments and use a bounded compact host-only output route marker, not caller provenance or a new Core business table.
- Existing `ReplyReference` captures only necessary original channel routing. Reuse original channel/sender/metadata in the routed ExternalInput context and a compact `[source_id,binding]` marker; origin remains canonical external and human_authorized remains false. Validate this host envelope fits existing metadata capacity; do not silently truncate or raise payload allowances.
- Reply lookup must happen before durable submit, not only before execution: otherwise a restart with the feature disabled could consume an accepted message and irretrievably lose its per-message outputs. ACK-loss duplicates compare the full canonical message plus actual scope against the already accepted routed input, never reselect its destination.
- Persisted output route with absent source policy remains pending, not delivered/cancelled. No callback/Agent/service starts merely to resolve it. Feature registration/start resumes pending transport after its original intent is available.

## Review Focus

1. A first real assistant message must reach/persist before a second model response or final turn release, and never be resent as aggregate final text: Task2 actual streamed-model barrier test.
2. Cancellation between durable begin and entering sender must prove not-sent/cancelled; after sender entry unknown cannot become not-sent: Task1 real output-store barriers.
3. Source/capability absent on receiver/app restart must not lose queued message boundaries, borrow a new recipient or execute side effects again: Tasks1/2 original routing/restart tests.
4. Forged source/provenance/same-ID different payload cannot claim an immutable outbox reply; same original-ID ACK recovery must retain old destination: Task2 actual ACP tests.
5. A stopped source can still finish accepted execution/output, while explicit notification cancellation leaves execution intact and fences future output: Task2 independent-operation tests.

### Task 1: Generic immutable automatic reply routing and Core output cancellation

**Files:** Create `packages/claw/src/xdog/claw/core/outputs/routes.py`, `packages/claw/tests/test_output_source_routes.py`; modify `core/outputs/{store,delivery}.py`, `core/runtime/{orchestrator,channel_adapter,input_adapter}.py` and existing output tests as needed. No source/domain imports in these Core ports.

**Interfaces:**

- Frozen `OutputRoute(source: str, reference: ReplyReference | None)` and generic registered ports `resolve(InputEnvelope) -> Awaitable[ReplyReference | None]`, `policy(OutputRecord) -> Awaitable[Literal['ready','pending','cancelled']]`. `Orchestrator.register_output_source(name, resolve, policy) -> Callable[[],None]` admits only trusted host composition; actual canonical outbox matching is the feature resolver's responsibility. Multiple conflicting claims fail before execution, not choose import order.
- `apply_route(envelope: InputEnvelope, route: OutputRoute) -> InputEnvelope` preserves scope/canonical message/images and false external human authority. For a real reference, use its original channel/sender/necessary metadata plus compact protected context marker `output=[source,binding]`. Caller metadata/provenance cannot become that marker. No reference means no destination/report, never group broadcast.
- `accept_external` freezes/resolves routing before submit/ACK; an existing routed external receipt is returned only after exact canonical message/scope/content comparison. A duplicate must not consult a later recipient/source resolver. Source absence after restart does not erase accepted routing.
- Core `_execute_input` uses persisted routed reference for ordinary existing `durable_reply` and reply-reference context. OutputDelivery preflight checks only generic registered policy; absent port is pending. It never queries observer/Goal state itself. Ordinary outputs retain their existing behavior.
- `OutputStore`v2 adds `cancelled` output state and a pending-only cancellation operation; actual sending/delivered/unknown attempts are never reset. Cancellation after proven not-sent preserves previous attempt facts. Check notification policy before begin and again before entering sender; cancellation/no-policy at that fence settles actual not-sent rather than unknown.

- [ ] Write/run RED tests with actual leased Core stores and sender barriers: frozen target/content/scope, canonical duplicate recovery without later rebind, forged context nested under provenance/metadata, metadata/image capacity, first/late cancellation, known-not-sent and unknown restart. No mock OutputStore/close or schema/source-text assertion counts as transport proof.

```python
row = await store.append(envelope, attempt_id, 0, binding, "first")
await store.cancel_pending(envelope.scope, (envelope.message.id,))
assert (await store.records(envelope.scope))[0].status == "cancelled"
assert not transport_calls
```

Run: `uv run pytest packages/claw/tests/test_output_source_routes.py packages/claw/tests/test_output_receipts.py -q -n 0` (use the actual existing output test filename when checking repository collection). Expected missing route/control RED, then all cases pass with ordinary behavior unchanged. Resolve any filename mismatch before executing, not by adding a facade.

- [ ] Implement small generic ports, protected route projection and output cancellation. Observe actual old-format refusal preserving bytes. Validate sender/result receipt distinctions and cancelled emitter cleanup without replay. Run whole9/strict9/Ruff/Flow/lock/diff; commit `feat(claw): route automatic replies through owned output receipts`.

### Task 2: Managed observer binding, streaming and independent report intent

**Files:** Modify `core/observers/{manager,journal}.py`, add `packages/claw/tests/test_observer_message_outputs.py`; retain/update actual recipient, pending report, Eyes scheduler/Unix ACP tests. Remove retired aggregate-only report send methods once no caller remains.

**Interfaces:**

- ObserverJournal resolves a reply only from the actual scoped precommitted outbox row whose canonical message exactly equals the external input, using the immutable saved creator ReplyReference. Neither observer name/source/provenance nor the ACP peer supplies that target. Resolve route even if source stopped or notifications cancelled after a real accepted intent; execution is a separate fact.
- ManagedObservers registers the generic reply/policy ports during owned capability initialization before resuming paused inputs, unregisters after owned shutdown, and explicitly resumes pending output transport after registration. It still calls the existing ACP handler for intake.
- Report policy returns cancelled only for separately explicit persisted notification cancellation; observer.enabled/source stop alone cannot cancel outputs or accepted input. Missing original outbox/policy is pending/review, never fallback to another peer.
- `_report` reads actual Core OutputRecords for this message/attempt, mirrors their delivered/pending/unknown/cancelled facts and never sends receipt.response. Current aggregate delivery pump/orchestrator direct-send helpers are removed rather than retained as alternate authorities. Input response stays read-only history/status.
- Observer journal new formatv3 (oldv2 refused byte-preserving) distinguishes its notification intent from Core delivery truth. Original cursor+whole pending Delivery remains atomic. Terminal source occurrence may release its cursor while original unsent report stays pending, preserving the earlier no-starvation fix.

- [ ] Write/run RED actual ACP/model-stream tests. Model emits first assistant text then waits before next call/final response; assert first actual original destination output/receipt is already visible, then second text, with no duplicate aggregate. Assert same original context/recipient/output IDs across offline recovery and ACK loss; unknown sends/crash are not retried. Fake labels/different payload conflict never select another target.
- [ ] Test actual source cancel and notification cancel independently at pending/running/sending fences. Accepted model/tool work persists and no message approval is introduced. Same-scope peer B cannot steal peer A reports; unavailable original stays pending.
- [ ] Implement adapters and remove aggregate sender/unused report state methods. Run whole9/strict9/Ruff/Flow/lock/diff and commit `refactor(claw): stream observer messages through core output delivery`.

### Task 3: Cold transport/output artifact evidence and documentation

**Files:** Extend existing installed Claw output/native/ACP consumers and create `docs/observer-message-output-verification.md`; update current AGENTS/README/site/47-item evidence. Rebuild affected distributions; do not promote unrelated pending audit requirements.

- [ ] Add external actual wheel-only scenario with original input reply binding, real observer/Unix ACP and multi-message Agent stream. Verify first send before final turn completion, persistent transport/no model replay, separately cancelled unsent reports and same recipient on restart. No source runtime injection or fake backend/store.
- [ ] Build affected wheels+sdists, Twine, full runtime/resource byte parity and existing Goal-only/two-host same UI plus ACP/native/output probes. Verify UI/ordinary base assistant behavior remains independent.
- [ ] Run all9 unfiltered, strict9/Ruff/Flow17/lock/diff; record exact commands, environment/concurrency and retained earlier timing/no-space observations. Commit `test(claw): verify durable per-message observer outputs`.

## Still not omitted

After this plan remove inert queue/debounce configuration declarations, settle any actual generic retired-context resource findings, audit every47 requirement and named artifact against current evidence, then one fresh-context whole-branch review and evidence-backed fixes. None of this authorizes push/merge/deploy or whole-goal completion before that audit.
