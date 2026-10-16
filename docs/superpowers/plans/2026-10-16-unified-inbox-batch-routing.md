# Unified Inbox and Batched Session Turns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Execution method remains Native: no implementation subagents or per-task reviewers; one independent whole-range review at the end. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Event → Router → extensible handler; all coding external messages enter durable Inbox without message approval; the sole controller consumes up to configurable N independent messages in one Agent turn.

**Architecture:** Separate Eyes handler registration/lifecycle from routing and ACP encoding. Retain Inbox as durable intake and controller as the only execution owner; add a frozen batch envelope without concatenating member messages. Preserve individual message/attempt/result facts while publishing shared model/tool output once per batch.

**Tech Stack:** Python 3.12+, uv, asyncio, frozen dataclasses, existing Pydantic configuration, Unix-local ACP/stdio, `agent-client-protocol>=0.12.1,<0.13; python_version < '3.15'`, existing flock/fsync/atomic publication, pytest, Ruff, strict mypy.

**Spec:** `docs/superpowers/specs/2026-10-16-unified-inbox-batch-routing-design.md`, approved by the user on 2026-10-16. This plan is awaiting its own user review. Product implementation has not started.

## Global Constraints

- Work in the original checkout `/data/workspaces/pyspace/xdog`, branch `feat/eyes-observation`; do not create a worktree or automatically push/PR/publish.
- Preserve Native execution and existing unrelated changes. Create this plan's own ignored SDD workspace/ledger; do not reuse or delete another plan's workspace.
- Eyes does not depend on agent/ai/coding/claw/flow. Generic Agent ACP does not depend on coding Inbox. No additional SDK upgrade or transport is needed.
- Source → Detector → Event → EventRouter → EventHandler. ACP is one registered handler, not a runtime/executor policy branch.
- Coding online/offline external messages always enter the same durable Inbox path. Inbox admission, consumption and starting a turn require no message approval or human wake credit.
- Tool operations still use the original PermissionManager. No approver and no explicit allow-all means restricted operations fail closed; external labels never become a human identity or permission grant.
- `max_messages_per_turn` defaults to 10, strict integer range 1..128, with 1 restoring single-message consumption. Batch encoded input maximum is 1 MiB; retain provider context/image validation.
- A batch is a fixed set of independent input messages, one Agent turn, one shared output stream, individual IDs/statuses. Do not concatenate member text, loop over single-message prompt calls, or insert history manually.
- Preserve human priority, same-class admission order, session/project/branch/generation fences, one writer lease, and full producer/tool/control/summary settlement.
- Pending/unstarted work can recover automatically. Running work becomes interrupted and is never automatically replayed. Failed/terminal work is not silently retried.
- Queued/duplicate ACK means durable intake, not model completion. Standard ACP prompt still waits for execution; do not use enqueue to hide an inactive prompt failure.
- Follow TDD and read every verification result. Optional/live provider/IDE exclusions must be disclosed, not represented as passing tests.

## Review Focus

Each of these risks must have the named owning tests before final review:

1. **Partial batch publication or terminal fsync failure:** no model starts until every member has a durable running fact; no member caller receives success until the entire finalization receipt settles. Tasks 2/4/6: `test_partial_running_publication_never_starts_batch`, `test_last_member_terminal_failure_reports_failure_to_all_waiters`.
2. **Shared batch cancellation across peers:** disconnect/cancel of one member cannot abort another member or falsely erase already admitted model input. Tasks 4/6: `test_active_shared_batch_peer_cancel_detaches_only_its_waiter`, `test_two_real_acp_peers_share_result_without_cross_cancel`.
3. **Owner starts before presentation/permission hooks are installed:** consumption is automatic only after owned setup settles; permission hooks remain authoritative for TUI/RPC and SDK-only owners. Tasks 5/7: `test_owner_activation_waits_for_setup_not_message_approval`, `test_real_rpc_tool_permission_response_during_automatic_batch`.
4. **Legacy state and durable ordering after process death:** old approvals are not authority, actual running work is not replayed, newly received messages retain persisted order after restart. Task 2: `test_legacy_migration_retains_attempt_facts_and_blocks_running_replay`, `test_intake_order_survives_restart_and_concurrent_writers`.
5. **Extension-handled input, images and mixed provenance:** batch APIs must not bypass per-input transformation/context capture, and UI must not repeat one response for every member. Tasks 3/7: `test_mixed_batch_preserves_images_and_input_transform_results`, `test_shared_native_batch_renders_each_input_and_one_response`.

## File/responsibility map

Paths with a package prefix below are rooted at `packages/<package>/src/xdog/<package>/`.

| Unit | Files | Responsibility |
|---|---|---|
| Eyes contracts/registration | new `eyes/handlers/contracts.py`, `eyes/handlers/registry.py`, `eyes/handlers/acp_config.py`; `eyes/models.py`, `eyes/loader.py` | Common handler definition/lifecycle and type-owned parsing; retain v2 ACP shape |
| Eyes routing | new `eyes/router.py`, `eyes/deliveries.py`; `eyes/executor.py`, `eyes/runtime.py` | Type-neutral route selection, immutable delivery identity, lifecycle/status dispatch |
| ACP implementation | `eyes/handlers/acp.py`, `eyes/acp_client.py`, `eyes/events.py` | ACP-only configuration, serialization, capacity and explicit connection recovery; no wake-credit gate |
| Durable Inbox | `coding/core/sessions/inbox_models.py`, `inbox_store.py` | Versioned state/sequence, stable intake, claims, attempt/batch association and recovery |
| Batch values/execution | new `coding/core/sessions/batches.py`; `models.py`, `turns.py`, `coding/core/agent_session.py` | Independent member preparation, shared turn, result/event envelope |
| Configuration | `coding/config.py`, `coding/core/settings_manager.py`, `coding/core/sdk.py` | Strict max-messages setting, layering and restoration |
| Scheduling | `coding/core/sessions/controller.py`, `store.py` | Batch selection, atomic sidecar transitions, scoped waits/cancel and full settlement |
| Automatic consumption | `coding/core/sessions/inbox_runtime.py`, `runtime.py`; new `coding/core/sessions/intake.py` | Common Inbox composition for all owners, bounded drain/watch and prompt ticket lookup |
| Protocol adapters | `coding/acp/backend.py`, `ingress_backend.py`, `coding/cli/enqueue.py` | One durable intake per message, standard prompt waits, shared output fan-out |
| Presentation/modes | `coding/core/inbox_commands.py`, `sessions/inbox_views.py`; native/RPC/print files in Task 7 | Remove approval UX; per-input and once-per-batch views, permission continuity |
| Verification/docs | Task 8 exact paths | Current public contract, evidence matrix, isolated artifacts and final review |

No generic handler definitions require ACP fields. Do not put test-only handler implementations in production testing exports. Keep fixture classes inside their tests/conftest.

## Shared interfaces and sequencing decisions

These are the contracts tasks consume; do not independently rename them in later tasks.

### Eyes (Task 1)

- Frozen `HandlerDef(id: str, type: str)` is the common definition base. Frozen `AcpHandlerDef` extends it with `connection`, `session`, `instruction`, `delivery`. Preserve `definition_data` v2 roundtrip.
- Registry parser signature is `(value: object, path: str, base_dir: Path) -> HandlerDef`; factory signature is `(definition: HandlerDef, cwd: Path) -> EventHandler`. `builtin_handlers() -> HandlerRegistry` returns the built-in registration. ACP definition parsing lives in lightweight `handlers/acp_config.py`; its create callback lazily imports the SDK-dependent ACP implementation only at activation.
- `HandlerRegistry.register(type_name, *, parse_definition, create)` accepts explicit parser/factory callables; duplicate/unknown types fail. `parse(value, *, base_dir, handler_registry=None)` uses the default built-in registry when not injected. Both parsing and runtime receive the same registry instance in API tests. Runtime child uses the deterministic built-in registration; arbitrary import/plugin loading is not introduced.
- `EventHandler` protocol: `async start() -> None`, `validate(delivery: Delivery) -> None`, `async deliver(delivery: Delivery) -> DeliveryResult`, `snapshot() -> HandlerSnapshot`, `async resume() -> bool`, `async close() -> None`.
- Frozen `HandlerSnapshot(state: str, details: dict[str, JsonValue])`; copy JSON at boundaries. Handler-specific facts such as session, delivery mode, owner generation and process liveness live in the snapshot, not generic runtime getattr branches.
- Move frozen `Delivery` to `eyes/deliveries.py`: fields `id`, `registration_id`, `route_id`, `events`; ACP instruction belongs to its handler definition. It is not a compulsory generic handler field.
- `EventRouter(definition)` exposes `route(events: tuple[Event, ...]) -> tuple[RoutedEvents, ...]`; frozen `RoutedEvents(route_id, handler_id, events)` retains matching event order. Router does not execute a handler or generate protocol payloads.
- `observe` keeps `deliver` injection and adds handler validation injection; executor enforces generic count/path bounds while each handler validates its actual encoded payload. Delivery retry/recovery capability is reported by the result, never inferred from `.delivery == 'enqueue'` in generic code.

### Frozen coding values (Tasks 3/4)

```python
@dataclass(frozen=True)
class TurnInput:
    value: SessionInput
    images: tuple[ImageContent, ...] = ()
    input_origin: str | None = None
    raw_text: str | None = None

@dataclass(frozen=True)
class TurnBatch:
    id: str
    session_id: str
    branch_id: str
    generation: str
    items: tuple[TurnInput, ...]

    @property
    def request_ids(self) -> tuple[str, ...]:
        return tuple(item.value.id for item in self.items)
```

- Add default `batch_id: str = ''` to `InputRecord` and `TurnResult` after their existing fields; add `batch_id: str = ''`, `request_ids: tuple[str, ...] = ()` to `SessionEvent`. Read old InputStore rows without those fields; never reactivate their historical interrupted entries as Inbox work.
- Per-input lifecycle events retain `request_id`. Shared `agent_event`/batch events have `batch_id` and complete `request_ids`; emit once, not once per member. ACP subscribers filter membership and project once per connection, UI filters batch identity/sequence.
- `AgentSession._execute_messages(inputs: tuple[TurnInput, ...], *, batch_id: str) -> AssistantMessage | None` runs the complete shared lifecycle. Existing `_execute_message` delegates a singleton to the same body, preserving special custom-context continuation semantics. Mixed batches cannot reinterpret a canonical continuation as a plain prompt; control-owned continuations remain non-batchable barriers.
- `run_batch(session, batch: TurnBatch, *, emit) -> TurnResult` is the default execute path. Keep `run_turn` only as a singleton adapter while migrating actual call sites, not a second model loop.
- `SessionController(..., max_messages_per_turn: int | None = None, execute_batch: Callable[[TurnBatch], Awaitable[TurnResult]] | None = None)`. Default resolves the owning session's effective setting. Migrate internal test injection from `execute`/`_execute` to batch injection; do not keep production adapters that run one old execute callback per member.
- `before_batch`/`after_batch` replace per-input native/RPC lifecycle hooks. Prepare/finalize UI/extension producer ownership once per batch. Individual input lifecycle/Inbox facts remain per member.

### Inbox (Tasks 2/5)

- v2 `InboxRecord` adds persistent `sequence: int`, `batch_id: str`; `InboxAttempt` adds `batch_id: str`. Allowed current states: pending, claimed, running, succeeded, failed, cancelled, interrupted. `claimed` is an internal reservation, not authority.
- Keep `enqueue`, `records`, `identity`, `scope_id`, and owner-only `recover`/`retire`. Add `reserve(session_id, message_ids, *, cwd, branch_id, generation) -> tuple[InboxRecord, ...]`, `begin_batch(session_id, records, *, batch_id, generation) -> tuple[InboxRecord, ...]`, `release_unstarted(session_id, message_ids, *, generation) -> None`, `retry(session_id, message_id, *, cwd, branch_id) -> InboxRecord`, and owner-only `cancel_pending(session_id, message_id, *, cwd, branch_id, generation) -> InboxRecord`. `finish` remains guarded by generation/attempt identity. Delete `approve` after all callers migrate.
- Batch byte accounting sums each independent canonical input/image-reference encoding; the shared batch envelope is not counted again against a legal singleton, so a previously valid message cannot stall solely due to batch metadata overhead.
- Assign sequence under existing Inbox lock from a private atomic/fsynced per-session counter. Persist gaps after a crash rather than reuse a value; order by sequence, never filesystem iteration or hashed message filename. Existing v1 lacks historical arrival order: migrate once in deterministic original-ID order and label that fact as legacy migration, never claim reconstructed real arrival timestamps.
- `begin_batch` writes every member's running fact before the model can start. A partial publication fails the whole scheduling receipt and starts zero model work. Recovery is conservative for any fact already marked running.
- `InboxCoordinator(controller, store)` no longer needs SessionRuntime to exist: it captures `controller.session`, generation, branch and actual lease. Shared composition function `attach_inbox(controller, store=None) -> InboxCoordinator` in `intake.py` supports runtime and SDK-only transient owners without creating a second controller/runtime.
- Coordinator API: `snapshot()`, `drain() -> tuple[Submission, ...]`, `submit_prompt(message: InboxMessage, *, peer_id: str) -> Submission`, `retry(message_id, *, peer_id) -> str`, `cancel(message_id, *, peer_id) -> bool`, `starting(batch: TurnBatch)`, `finished(batch: TurnBatch, result: TurnResult)`, `retire()`. `submit_prompt` and automatic drain join the same message/attempt ticket, never resubmit an ID through two paths.
- Drain reserves available capacity under controller control, submits accepted requests, then returns; never await their model results inside that control. Polling is only intake/readiness, not a second executor. All async filesystem work runs off the event loop and retains cancellation settlement receipts.

### Planned policy choices

1. No new CLI flag is required: expose `max_messages_per_turn` in global/project JSON, persisted session settings and SDK overrides; existing attach rejects attempts to configure the remote owner. Explicit override > restored session value > project > global > 10. Values must be validated before model/bootstrap where configuration is explicitly provided. Existing config loaders currently swallow errors, so the new setting must not silently fall back when explicitly invalid.
2. `SessionRuntime.start` and transient SDK owner composition initialize Inbox behind an owner-activation setup barrier. Native/RPC install callbacks/permission sinks and take their initial display snapshot before releasing it; SDK/headless releases after normal owner setup. This is automatic lifecycle setup, never user approval. Closing a transient SDK owner is still explicit owner-lifetime termination, not a hidden daemon that drains after caller shutdown.
3. A batch's shared stream belongs to participating request waiters only. A peer's cancellation may detach its waiter while the durable execution remains running/succeeded; represent subscription outcome separately, not by changing the execution fact to cancelled. TUI local abort can still cancel the entire batch.
4. External prompt message IDs are created once at the authoritative owner and retained by the attachment; model-free ingress forwards prompt without independently enqueuing a second copy. Explicit enqueue retains the original caller-supplied stable ID.

---

## Task 1: Generic Eyes handler registry/router and removal of wake-credit fallback

**Files:** Create `packages/eyes/src/xdog/eyes/{router,deliveries}.py` and `handlers/{contracts,registry,acp_config}.py`; modify `models.py`, `loader.py`, `events.py`, `executor.py`, `runtime.py`, `acp_client.py`, `handlers/acp.py`, `handlers/__init__.py` in that source root. Create `packages/eyes/tests/test_event_router.py`, `test_handler_registry.py`; update existing loader/executor/runtime/ACP tests.

**Produces:** Eyes interfaces above. ACP-only connection serialization moves behind its registered implementation. Preserve transport and explicit unknown-result recovery.

- [ ] **1. Write registry/router RED tests with a non-ACP handler.** Define a frozen test-only subclass containing `id`, `type='record'`, `label`; register a parser/factory that do not accept connection/session/instruction. Route the same file event to ACP and record handlers, preserving independent route/delivery IDs. Pin unknown/duplicate types, normalized roundtrip and generic status/cleanup. Include this direct router assertion using production Event/FileEvidence:

```python
from xdog.eyes.models import Event
from xdog.eyes.router import EventRouter

def test_router_preserves_event_order_and_matches_two_handlers(two_handler_definition):
    events = tuple(Event(str(i), 'files', 'changes', 'file.modified', float(i), ()) for i in range(2))
    routed = EventRouter(two_handler_definition).route(events)
    assert [(r.route_id, r.handler_id) for r in routed] == [('review', 'notify'), ('audit', 'record')]
    assert all(r.events == events for r in routed)
```

Create `two_handler_definition` fixture in `test_event_router.py`: use existing observer_data, append handler `{'id':'record','type':'record','label':'audit'}` and route `{'id':'audit','detector':'changes','handler':'record','event_types':['file.modified']}`, then parse with the explicit test registry.

- [ ] **2. Observe RED.** Run `uv run python -m pytest packages/eyes/tests/test_event_router.py packages/eyes/tests/test_handler_registry.py -q -n 0`. Expected: missing registry/router behavior; fix fixture/import typos rather than treating collection mistakes as sufficient RED.
- [ ] **3. Implement frozen common contracts and registered parsing.** Use the shared definition subclasses and factory API; move ACP parser/connection logic out of generic loader dispatch. Register built-in ACP lazily so validate/help do not import the optional SDK. `definition_data` serializes dataclasses including the ACP subtype without adding registry state to definitions.
- [ ] **4. Implement pure matching and generic lifecycle.** Move Delivery as specified; replace executor matching loop with `EventRouter.route`; invoke registered handler validate before creating bounded pending delivery. Replace runtime's AcpHandler type/constructor/status/process access with common lifecycle/snapshot. A failed route must not erase another route's state or mistake a non-ACP handler for an enqueue connection.
- [ ] **5. Write and observe no-credit RED, then remove the gate.** Add `test_two_prompt_deliveries_need_no_human_credit` against a real official-SDK-only peer that supports ordinary prompts and no XDOG extensions. Deliver two deterministic messages without resume and assert two final responses. Remove `wake_budget`, capability-based credit inference and automatic fallback pause from AcpHandler; keep invalid-generation, outcome-unknown, quota and explicit connection-recovery behavior. Do not automatically replay an uncertain prompt.
- [ ] **6. Verify and commit.** Run `uv run python -m pytest packages/eyes/tests -q -n 0`, `uv run ruff check packages/eyes`, and `uv run mypy --strict -p xdog.eyes`. Expected: all Eyes contracts pass; older tests asserting one-turn credit are migrated to consecutive auto-delivery plus retained unknown-result/no-replay coverage. Run `uv run python scripts/test.py` and report any intermediate cross-package migration failures by name. Commit `refactor(eyes): route events through registered handler contracts`.

## Task 2: Inbox v2 durable ordering, non-approval claims and migration

**Files:** Modify `packages/coding/src/xdog/coding/core/sessions/inbox_models.py`, `inbox_store.py`; create `packages/coding/tests/test_inbox_v2.py`; extend `test_inbox_store.py`, `test_inbox_crash.py` (same tests directory).

**Consumes/produces:** Existing private locked Inbox filesystem; produces v2 interfaces above without ACP/AgentSession imports. Keep old API temporarily only until Task 5 replaces its consumers; it cannot become a public auto-execution bypass.

- [ ] **1. Write independent new store tests.** Use SessionManager directly, not the old `box` fixture's approval assumptions:

```python
def test_intake_records_pending_in_arrival_order_after_reopen(tmp_path):
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore
    manager = SessionManager(tmp_path / 'sessions')
    data = manager.create_session(working_dir=tmp_path)
    store = InboxStore(manager._dir, tmp_path / 'inbox')
    for key in ('z-last-lexically', 'a-first-lexically'):
        receipt = store.enqueue(InboxMessage(data.session_id, tmp_path, key, key, (), 'peer', {}))
        assert receipt.record.status == 'pending'
    rows = InboxStore(manager._dir, store.root).records(data.session_id, tmp_path)
    assert [r.message.message_id for r in rows] == ['z-last-lexically', 'a-first-lexically']
    assert rows[0].sequence < rows[1].sequence
```

Add parametrized v1 raw fixtures for pending_review, approved with an old attempt, running and terminal; preserve original message/attempt identity. Add multi-process enqueue ordering and counter-fsync faults, duplicate intake no extra sequence, quota/namespace/privacy regressions, and the Review Focus legacy/crash tests.

- [ ] **2. Observe RED.** Run `uv run python -m pytest packages/coding/tests/test_inbox_v2.py -q -n 0`. Expected: pending/order/v2 transitions missing, not a fabricated missing model.
- [ ] **3. Implement v2 codec and sequence publication.** Validate strict state/version/fields; snapshot nested JSON; keep 1 MiB message and 2 MiB record bounds. A pending message explicitly cancelled before execution may have no attempt; never invent an executed attempt solely to satisfy an old terminal codec assumption. Publish counter and record under one lock; unused sequence gaps are safe. Persist legacy mapping before releasing recovery lock; unknown versions/states fail closed. Do not reorder new arrivals by hashed IDs.
- [ ] **4. Implement reserve/begin_batch/release/retry.** Check actual session/cwd/branch, generation, expected attempt identities and distinct IDs. Reserve only pending; cancel_pending atomically terminalizes only unstarted work under the current owner context. Coordinator validates trusted local/operator or actual prompt ownership before this API; self-reported source labels are insufficient. Interrupted needs explicit retry, which returns it to pending while retaining old attempt. Validate the entire selected set before publishing; no persisted permission fields or client-supplied owner/batch authority. begin_batch starts zero work itself.
- [ ] **5. Observe crash/fault RED then implement recovery.** Child processes call `os._exit` after pending, after reserve and after all running publications; assert automatic eligibility only for never-started work. Inject failure on the last member's running publication and verify no model callback has run. Retire claimed → pending, running → interrupted; retain historic attempts. Heal publication-before-folder-move facts without duplicate authoritative files.
- [ ] **6. Verify and commit.** Run new v2/store/crash files; update old store tests to new APIs without deleting dedup, mutation, concurrency, fsync, security or unknown-effect assertions. Run all coding tests and full root `scripts/test.py`; record any still-pending integration failures explicitly. Commit `feat(coding): persist ordered inbox claims without message approval`.

## Task 3: Strict batch configuration and one AgentSession turn for independent messages

**Files:** Create `packages/coding/src/xdog/coding/core/sessions/batches.py`; modify `config.py`, `core/settings_manager.py`, `core/sdk.py`, `core/agent_session.py`, `core/sessions/models.py`, `turns.py`. Create `packages/coding/tests/test_batch_config.py`, `test_batch_turns.py`; extend config/SDK/session-turn/extension/context/image tests.

**Produces:** TurnInput/TurnBatch, `_execute_messages`, `run_batch`, default/range/layering above. No change to generic Agent's already working list prompt is needed unless the real boundary tests expose a specific bug.

- [ ] **1. Write batch/config RED.** Directly exercise a real coding_session with deterministic model:

```python
async def test_five_inputs_are_five_history_messages_in_one_turn(coding_session):
    from xdog.agent.session_input import SessionInput
    from xdog.ai.types import AssistantMessage, TextContent, UserMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.coding.core.sessions.batches import TurnInput
    seen = []
    def stream(model, context, options):
        seen.append(tuple(context.messages))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='batch answer'),)))
    coding_session.agent.set_stream_fn(stream)
    inputs = tuple(TurnInput(SessionInput(f'm{i}', 'human', 'coding', f'message{i}', (), {}))
                   for i in range(1, 6))
    await coding_session._execute_messages(inputs, batch_id='batch-test')
    assert len(seen) == 1
    assert len([m for m in coding_session.messages if isinstance(m, UserMessage)]) == 5
    assert len([m for m in coding_session.messages if isinstance(m, AssistantMessage)]) == 1
```

Also assert ordered exact text in model context and reloaded history, not only counts. Parametrize strict values `True`, `False`, `0`, `-1`, `129`, `'10'`, `1.5`; test default 10, project/global/session/explicit override precedence, saved-session restore and failed config loading visibility.

- [ ] **2. Observe RED.** Run `uv run python -m pytest packages/coding/tests/test_batch_config.py packages/coding/tests/test_batch_turns.py -q -n 0`. Expected: missing batch API or strict setting, not unrelated provider initialization.
- [ ] **3. Wire strict configuration.** Reuse one strict positive bounded integer validator across config, saved session and SDK. Persist effective setting in SessionSettings. Add no CLI flag; existing SDK overrides and config files supply it. Do not swallow an explicitly invalid batch setting through GlobalConfig/ProjectConfig fallback; preserve unrelated existing fallback behavior.
- [ ] **4. Factor shared lifecycle.** Construct one ordered `list[AgentMessage]`: each human input has its own text/image content; each external input uses its own canonical encode_input. Capture human context once per batch, validate all images before admission and again after metadata-changing hooks, rebuild/compact once, and call `agent.prompt(message_list)` once. Preserve custom-context continuation as an isolated non-batchable control. Keep singleton `_execute_message` API semantics.
- [ ] **5. Pin lifecycle/extension edge cases before implementation.** `turn_start` fires once with structured `inputs`/batch ID; retain legacy scalar `user_input` for singleton only, never concatenate batch text into that key. Per-input input-transform and handled-feedback behavior remain independent. Test one input handled by extension while another reaches the model, all inputs handled (no invented model call), a transform rejection before model admission, mixed external provenance, per-member image association, provider/context rejection, and cancellation during compaction. Verify tools still reach original permission hooks.
- [ ] **6. Verify and commit.** Run new files, `test_session_turns.py` and existing SDK/image/context/extension regressions; run coding/full-root tests and record migration failures. Commit `feat(coding): execute independent messages in one agent turn`.

## Task 4: Controller batch scheduler, result envelope and scoped cancellation

**Files:** Modify `packages/coding/src/xdog/coding/core/sessions/controller.py`, `models.py`, `store.py`, `batches.py`, `turns.py`. Create `packages/coding/tests/test_session_batches.py`; extend `test_session_controller.py`, `test_session_mode_routing.py`, `test_acp_review_regressions.py`.

**Consumes:** Tasks 2/3 contracts. **Produces:** Fixed batch scheduler, once-per-batch events/hooks, per-member results, no wake budget. Existing `submit`/accepted/result API remains usable.

- [ ] **1. Write scheduling RED with real barriers, not sleeps.** Hold an async controller control operation until five submissions have durable accepted receipts, release it, await all results, and inspect actual Agent context/history. Test 5 → one turn at default and 5 → 3+2 at limit 3. Use these helper semantics in the test file:

```python
async def admit_while_control_is_blocked(controller, values):
    import asyncio
    entered, release = asyncio.Event(), asyncio.Event()
    async def hold():
        entered.set()
        await release.wait()
    receipt = controller.control(hold, name='test-ready-barrier')
    await asyncio.wait_for(entered.wait(), 3)
    try:
        tickets = [controller.submit(value, peer_id='test') for value in values]
        await asyncio.wait_for(asyncio.gather(*(asyncio.wrap_future(t.accepted) for t in tickets)), 3)
        return tickets
    finally:
        release.set()
        await asyncio.wrap_future(receipt)
```

The test fixture owns controller close in finally; the helper does not create a production-only testing hook. Add max=1/default, count/aggregate-byte split, arrival during active batch, human priority, navigation, non-batchable continuation, cross-thread submission and unrelated producer barriers.

- [ ] **2. Observe RED.** Run `uv run python -m pytest packages/coding/tests/test_session_batches.py -q -n 0`. Expected: single-job execution/budget behavior differs from the required batch membership and model-call count.
- [ ] **3. Implement selection and shared execution.** Replace `_current` with an active batch/member index; preserve a compatibility current-request accessor only where single-input callers genuinely need it. `_pump` never selects uncommitted admissions, does not wait to fill N and does not mutate the selected batch. Remove `_budget`, `_budget_targets`, `_needs_credit`, credit grants and permission-based resume paths. Keep generic readiness/control fences and human-draft suppression. Execute `run_batch`, not a loop over `run_turn`.
- [ ] **4. Persist and publish.** Add InputStore `transition_batch(request_ids, status, *, batch_id, error='')` with one atomic sidecar commit. Store member running facts before entering the model; publish per-input lifecycle plus a shared once-per-batch stream. Snapshot batch membership/generation; never use mutable global UI state as request identity.
- [ ] **5. Write failure/cancel RED then implement settlement.** Pin all Review Focus cancellation/finalization tests: queued single cancel; active shared peer detach; singleton cancel; local abort all; disconnect while terminal persistence is blocked; repeated cancellation of control/summary cleanup; last-member terminal write failure. Finalize every durable receipt before settling any caller result, collect all cleanup failures, mark controller broken on unresolved persistence and start no successor batch. Avoid duplicated assistant results in mode-independent event subscriptions.
- [ ] **6. Verify and commit.** Run batches/controller/turns/mode-routing/review-regression tests, coding and all root paths. Tests that assumed one automatic turn become automatic consecutive batches, while security/settlement assertions remain. Commit `feat(coding): batch queued inputs under one session owner`.

## Task 5: Automatic Inbox consumption for all owner modes

**Files:** Create `packages/coding/src/xdog/coding/core/sessions/intake.py`; modify `inbox_runtime.py`, `runtime.py`, `core/agent_session.py`, `sessions/controller.py`. Create `packages/coding/tests/test_inbox_auto_consume.py`; migrate `test_inbox_controller.py`, extend `test_inbox_crash.py`, `test_session_ownership.py` and `test_acp_optional_imports.py`.

**Consumes:** Ordered v2 Inbox, batch scheduler. **Produces:** Shared composition/drain/submit_prompt API, no approval service or scheduler duplication.

- [ ] **1. Write automatic-owner RED.** Before creating the runtime, enqueue five messages using the actual coding_session identity. Start it behind a setup reservation, assert no model before setup release, then assert automatic processing into one batch with no permission callback for message admission. Cover bound=True/False, SDK-only transient owner, dedicated native loop, RPC setup and new messages arriving during a blocked turn. The permission spy must delegate real tool policy; do not monkeypatch the entire permission manager into an allow-all stub.
- [ ] **2. Observe RED.** Run `uv run python -m pytest packages/coding/tests/test_inbox_auto_consume.py -q -n 0`. Expected: current owner only snapshots pending review and never consumes automatically.
- [ ] **3. Implement common owner composition.** `attach_inbox` binds the actual lease/controller once; runtime and `_acquire_owner` use it without calling `enable_runtime` to create a second owner. Install setup reservation before any polling/drain. Release it after native/RPC callback/permission setup or normal SDK setup; it is never user approval. Retain SDK ref-counting and complete prior-close receipt fence.
- [ ] **4. Implement bounded single-owner drain.** Snapshot pending current-context records in durable order, reserve only available slots, submit/join actual tickets and return accepted receipts. Do not await results within controller control. A live refresh can publish snapshots while model work is busy; avoid a blocking drain hiding status. Coalesce simultaneous refreshes with one retained drain receipt; on queue-full leave work pending rather than mark success/drop it.
- [ ] **5. Replace approval and implement recovery/teardown.** Remove coordinator approve/review methods, synthetic BeforeToolCallContext and selected-credit grants; migrate tests preserving exact context/mutation checks as intake/claim CAS. Retry interrupted only on explicit command without virtual permission call. Context rebind retires old claimed attempts before activating new Inbox. Close cancels watch, settles all filesystem workers/drain receipts and releases claims before lease/resources. Never run a hidden model after a transient owner has closed.
- [ ] **6. Verify and commit.** Run auto-consume/controller/store/crash/ownership/optional-import tests, coding and root tests. Use real `os._exit` owner process for claimed/running recovery; no actual model/provider needed. Commit `feat(coding): automatically consume durable session inbox messages`.

## Task 6: Unified ACP intake and shared-batch responses

**Files:** Modify `packages/coding/src/xdog/coding/acp/backend.py`, `ingress_backend.py`, `client.py`, `server.py`; update `core/sessions/intake.py`, `inbox_runtime.py`, `inbox_views.py`, `cli/enqueue.py`. Create `packages/coding/tests/test_acp_batch_intake.py`; extend existing ACP backend/ingress/offline/bridge/CLI tests and `packages/eyes/tests/test_acp_enqueue.py`.

**Consumes:** submit_prompt joins existing Inbox/controller tickets; once-per-batch event envelope. **Produces:** Every external prompt gets one durable intake and correct per-peer completion, enqueue remains model-free.

- [ ] **1. Write real SDK/Unix RED.** Start existing private receiver and real owner, submit two prompt requests from separate peers while an owner control barrier is held, plus durable enqueue messages, release it and assert one shared batch/one assistant output per peer. Inspect Inbox: one record per original message, same batch ID, no double ingress/owner enqueue. Nonparticipating third peer receives no output. Use a stream barrier to prove enqueue ACK arrives before model completion while prompt stays pending.
- [ ] **2. Observe RED.** Run `uv run python -m pytest packages/coding/tests/test_acp_batch_intake.py -q -n 0`. Expected: prompt bypasses Inbox or response correlation is still per-single-job.
- [ ] **3. Route all external inputs through authoritative owner intake.** Local CodingBackend constructs InboxMessage and uses coordinator submit_prompt; remote ingress forwards standard prompt without generating another message ID or directly scheduling Agent. Preserve session binding/generation/cwd checks before intake; no inactive prompt → enqueue fallback. CLI attach remains the same ACP client path. Explicit enqueue/status remain import/model-free and never acquire writer ownership.
- [ ] **4. Correlate shared stream and waiting lifecycle.** Subscribe by selected request membership/batch ID, publish once per peer, bound output buffers, retain complete settlement on cancellation. PromptResponse includes XDOG request_id/message_id/batch_id/status but its stopReason remains normal ACP end_turn/cancelled. A peer's waiter-detached state is not a false durable cancelled execution. Terminal persistence failure errors all waiters. Enqueue disconnect cannot revoke durable pending work; distinguish transient prompt subscriptions from persistent Inbox facts.
- [ ] **5. Remove message-resume contract.** New CodingBackend/IngressBackend no longer advertise permission-bearing resume. Report `automatic_budget: false` for old metadata readers if retaining that field, never claim that a new human turn is required. Unknown old resume calls return explicit unsupported-method errors. Eyes data connection resume remains separate and does not grant model credit. Generic agent ACP retains its independent standard behavior.
- [ ] **6. Verify and commit.** Run new tests plus actual backend/bridge/ingress/offline/CLI/SDK tests, Eyes enqueue contracts, then full coding/root suites. Keep stable-ID conflict, same-address replacement-store and unknown prompt no-blind-retry cases. Commit `refactor(coding): route ACP messages through the durable batch inbox`.

## Task 7: Native/RPC/print presentation and no-approval commands

**Files:** Modify `packages/coding/src/xdog/coding/core/inbox_commands.py`, `core/sessions/inbox_views.py`, `core/session_manager.py` only if required for batch history metadata; `modes/interactive/{session_events,session_presenter,interactive_mode}.py`; `modes/rpc/{session_events,rpc_mode,inbox,queued_inputs}.py`; `modes/print_mode.py`. Create `packages/coding/tests/test_batch_display.py`; extend native queue/history/display/inbox/RPC/print/SDK permission and compaction tests.

**Consumes:** Shared batch envelopes/hooks, per-member histories and automatic Inbox activation. **Produces:** Five input entries plus one response, no duplicate scheduler/approval UI, existing real tool approval remains functional.

- [ ] **1. Write live/replay RED.** Real ChatLog/SessionPresenter render five independent input components and one shared assistant/tool stream. Reopen from saved history and assert the same counts, sources/IDs and batch association. Mixed native + external batch must not repeat the output through both adapters. RPC emits ready before initial snapshots and correlates each input to one batch stream.
- [ ] **2. Observe RED.** Run `uv run python -m pytest packages/coding/tests/test_batch_display.py -q -n 0`. Expected: current adapters expect one display request, lack batch identity or show old approval status.
- [ ] **3. Migrate one-per-batch lifecycle.** Replace native/RPC before_turn/after_turn hooks with before_batch/after_batch. Scope permission handlers, after-run compaction producers, extension dialogs and stream dedup by originating batch rather than arbitrary first member or current global stamp. Preserve all local queue IDs and edit/move/remove controls for still-unselected human inputs.
- [ ] **4. Remove approval commands/text.** New `/inbox` supports list/show/cancel/retry. `approve` gives a clear obsolete-command usage error; it must not silently run a duplicate task or ask permission. Read-only inspection remains nonblocking and context-checked. Cancel only pending/claimed-unstarted records; active shared-batch request cancellation follows Task 4 semantics and reports that execution was not withdrawn. Show actual blocked branch/context reasons, not pending-review prompts.
- [ ] **5. Pin tool permission/mode errors before implementation.** Real RPC reader must accept permission_response while a mixed batch runs; native approval dialogue uses the originating batch after a new message arrives; default headless restricted tool fails, explicit allow-all still works. All-handled extension batches report system feedback, not invented assistant answers. SDK raises and print returns nonzero on finalization failure; RPC retains actual partial content plus explicit error. TUI exit waits summary/producer settlement and never closes independently managed ingress.
- [ ] **6. Verify and commit.** Run display/native/RPC/inbox/SDK/print/compaction/queue regressions plus full coding/root suites. Delete obsolete approval-only assertions only when replaced by automatic-delivery and retained tool-permission tests. Commit `refactor(coding): present batched inputs without message approval`.

## Task 8: Full external-message closure, documentation, artifacts and independent review

**Files:** Extend `packages/eyes/tests/test_end_to_end.py`, `test_runtime.py`, `packages/coding/tests/test_acp_batch_intake.py`, `test_batch_display.py`, `test_acp_optional_imports.py`; update `scripts/verify_acp_artifacts.py`, `AGENTS.md`, `docs/acp-session-routing.md`, `docs/eyes-verification.md`, `packages/{agent,coding,eyes}/README.md`, `packages/eyes/src/xdog/eyes/examples/source-test-review.json`, and the existing packaged `eyes-observers/SKILL.md` (locate with `git ls-files '*eyes-observers/SKILL.md'`). Create `docs/unified-inbox-batch-verification.md` and review resolution notes. Mark conflicting old designs/verification as historical without rewriting their recorded test results.

**Consumes:** All previous tasks. **Produces:** Actual requirement evidence for spec acceptance 1..12, current skills/protocols and fresh final review.

- [ ] **1. Write end-to-end RED before final wiring.** Actual file changes → EventRouter → ACP handler → explicit ingress → Inbox → owner batch → TUI live/reload. Stop owner, queue messages, start owner, prove automatic five-input batch without approve/resume. While online owner is blocked, queue five and assert next turn gets all five, new arrivals after selection form next batch. Also test headless SDK-only owner and two simultaneously observing routes without restored credit gates.
- [ ] **2. Observe RED, then finish missing wiring only.** Run the exact new E2E test names with `uv run python -m pytest packages/eyes/tests/test_end_to_end.py packages/coding/tests/test_acp_batch_intake.py packages/coding/tests/test_batch_display.py -q -n 0`. Expected eventual GREEN proves real closure, not a mocked delivery callback. Keep receiver failure/explicit reconnection, stable namespace and unknown-result behavior.
- [ ] **3. Update docs and packaged skill.** Document router/handler interface, online/offline auto-consumption, strict batch limit/config layering, independent messages/shared output, scoped cancellation, migration/unknown effects and tool-only approvals. Loading the skill starts nothing; receiver absence is unavailable. Update artifact smoke expected state pending (not pending_review), and add wheel-only real owner batch test without live provider. Invoke writing-skills before editing skill; use existing deterministic consumer checks where adequate, do not dispatch unauthorized implementation agents.
- [ ] **4. Run full source verification.** From repository root:

```bash
git diff --check
uv run python scripts/test.py
uv run ruff check packages scripts/verify_acp_artifacts.py
uv run mypy --strict -p xdog.ai -p xdog.agent -p xdog.tui -p xdog.flow -p xdog.site -p xdog.claw -p xdog.coding -p xdog.eyes
uv run xdog-flow test packages/flow/examples/ --allow-script-stub
```

Expected: all configured paths and checks pass. Audit skips with `uv run python -m pytest packages/agent/tests packages/ai/tests packages/coding/tests -q -rs`; list actual reasons, no new feature skips concealed as optional integration. Report any failure by name until fixed.

- [ ] **5. Rebuild and verify installed artifacts.** Use fresh output and existing reproducible script:

```bash
DIST=$(mktemp -d /tmp/xdog-batch-dist.XXXXXX)
for package in ai agent tui coding eyes; do
  uv build --package "xdog-$package" --out-dir "$DIST" || exit
 done
uvx twine check "$DIST"/*.whl "$DIST"/*.tar.gz
uv run python scripts/verify_acp_artifacts.py "$DIST"
```

Expected: five wheels/five sdists pass Twine, wheel-only Eyes has no harness dependencies, agent-only official SDK stdio remains functional, coding no-owner intake constructs no AgentSession and a separate deterministic wheel-only owner consumes multiple independent inputs without message approval. Do not enable real provider credentials or invoke an external IDE. Record artifact hashes/interpreter and compare shipped Python members to final source.

- [ ] **6. Independent review and one TDD fix pass.** Commit verified implementation; request one fresh read-only reviewer for base `8626efc` through actual implementation HEAD, including this plan/spec and ledger rulings. No implementation delegation, model override, extra worktree or per-task review. Review all tasks, not only docs. Re-grade findings by user impact, fix Important/Critical in one RED→GREEN pass, run full suite again and rebuild affected artifacts after source fixes. Record declined-item dispositions, no fabricated second reviewer approval.
- [ ] **7. Completion audit and commit.** `docs/unified-inbox-batch-verification.md` maps each spec acceptance 1..12 and all five Review Focus risks to authoritative tests/process/disk/protocol evidence, including exact current commit. Verify obsolete host remains absent; no synthetic approval, credit fallback or single-input execution loop survives in active paths. Record untested actual provider/IDE and any real skips. Commit `docs: verify unified inbox routing and batched session turns`. Run task-done with whole-root tests; only mark active goal complete after every required artifact/check/review is evidenced. Keep feature branch, no push/PR/merge/publish.

## Coverage and self-review

| Spec requirement | Owning task(s) |
|---|---|
| §4 / acceptance 1 generic handler routing and no ACP-specific executor | 1 |
| §5 / acceptance 2,3 online/offline automatic intake | 2,5,6,8 |
| §6.1 / acceptance 5,6 limit/order/config/single owner | 3,4,5 |
| §6.2 / acceptance 4 five independent inputs, one real turn | 3,4,8 |
| §5.3 / acceptance 7 no message approval, actual tool policy | 1,4,5,7 |
| §6.3 / acceptance 8 shared state/output/scoped cancel | 4,6,7 |
| §5.2 / acceptance 9 legacy/crash recovery | 2,4,5 |
| acceptance 10 dedup/namespace/bytes/context/privacy/leases | 1,2,3,4,6,8 |
| §7 / acceptance 11 true prompt completion/compatibility | 3,6,7,8 |
| acceptance 12 docs, skill, all checks/artifacts/review | 8 |

Self-review: all shared APIs are declared before consumption; new files have bounded responsibilities; all public behavior changes have named RED contracts and existing regressions to preserve. No additional CLI/transport/handler product scope is introduced. SDK-only ownership and shared waiter cancellation are explicit, not postponed to unspecified integration. Each intermediate full-suite failure must be reported and resolved before final completion. Execution order is Task 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8, with no new approval gate between tasks after the user approves this written plan.
