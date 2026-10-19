# Claw Optional Goal Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline continuously. User approved the design and continuous execution; no further approval gate, no worktree and no per-task reviewer.

**Goal:** Replace active Claw-owned Goal policy/storage with the same optional GoalService already exercised in Coding, keeping Claw a complete ordinary assistant by default.

**Architecture:** A group-scoped ClawGoalHost bridges existing supervised children and the application-owned durable Inbox. Group ownership survives conversation rotation, while actual worker references retain their original transcript/branch execution location. Core conversation/prompt/Gateway/ACP/UI paths stop knowing Goal lifecycle policy; Goal owns its separate journal and explicit operations.

**Tech Stack:** Python3.12 async frozen data, existing GroupController/InputService/ChildSupervisor/ChildStore, xdog-goal kernel, real private-file/channel execution with scripted model transport.

**Spec:** docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md sections8–13.

## Global Constraints

- Current checkout, preserve unrelated changes; no user-service/data/provider access, old-format upgrade, worktree, push/merge/publish.
- Updated user decision after Task1: remove `goals_enabled` from both hosts. Installed Goal automatically supplies capability and built-in skill; no package means ordinary chat/memory/tools/history without Goal storage. Package presence never delegates autonomy or starts workers. The completed Task1 below records its original flag-based acceptance; Task2 replaces that obsolete control.
- Exactly one group execution owner and existing child tool loops. No Goal polling/planner/turn-end reviewer, second main Agent or unfinished-Goal context-rotation veto.
- Parent readonly/MCP/extension gates unchanged; only actual bound context/report objects obtain their narrow data capability, never a tool-name exemption. Reviewer readonly is real.
- Reports submitted != accepted task != completed Goal. Only explicit request_complete reviews; source/notification/worker cancellation stay explicit and separate.
- Goal owns all Goal data/leases/outbox. No compatibility facade or old/new dual authority at the end of this cutover. Unsupported legacy storage is refused byte-preserving, not silently ignored in favor of an empty journal.
- Actual shutdown joins execution/storage before resource release; views/UI never own business lifetime.
- Coding's proven adapter remains exact session/branch. Do not copy that ownership rule into Claw's deliberately stable group scope.

## Review Focus

1. Rotation between worker settlement and explicit report acceptance must retain the original actual execution and never build/replay an offline Agent: task1 real old ChildStore test.
2. A forged role label or child ID in another session/group cannot borrow report/context authority: task1 simultaneous admissions and exact reference fences.
3. Paused/cancelled/complete Goal state or an Eyes event cannot implicitly stop/restore another resource: task1 independent operations and ordinary-turn/no-review tests.
4. Startup with legacy/unknown/corrupt Goal state must refuse before creating substitute truth: task1 byte-retention/cold-base tests, task2 wheel-only check.
5. Extension and UI failure/unload must not close business service, grant user authority or leak a view into another owner: task1 headless/permission tests; shared UI protocol follows in its own plan.

### Task 1: Actual group-scoped host, opt-in and active legacy cutover

**Files:** Create `packages/claw/src/xdog/claw/core/runtime/goal_adapter.py`, tests `test_goal_host.py`/`test_goal_host_recovery.py`. Modify `config.py`, `core/{__init__,types}.py`, `core/tools/registry.py`, `core/planning/__init__.py`, `core/runtime/{group,session,child_agents,orchestrator,gateway}.py`, `core/prompt/{builder,sections,templates}.py`, `core/observers/manager.py`; remove active native Goal UI branches from `channels/tui/tui_app.py` and their wire emitters. Migrate/remove obsolete legacy integration tests only with mapped replacement evidence.

**Interfaces:**

- `ClawGoalHost.open(runtime, inputs)` is trusted async capability startup. `.service` is the same GoalService, `.owner` is stable Core inbox namespace + group + workspace, not current transcript ID; `.attach(session)` installs a bound parent tool and independent child-status subscription into the existing session; `.aclose()` closes only admitted Goal jobs/store after the host has joined actual children.
- `GroupRuntime.goal_host` is optional capability composition, not a GoalManager facade; no `goals_summary`, `has_unfinished_goals`, `pop_goal_notifications` or core Goal table. Runtime lazily initializes only when global opt-in is true and the existing application input service is available. Plain direct runtimes remain usable with the default false.
- Host-only one-use task-local admission fixes each WorkerSpec/ReviewerSpec to the actual ClawChildren owner+child ID. Factory-created bound tools bridge to the existing host loop; the selected tool object in `ctx.context.tools` supplies the identity check before and after extension hooks.
- Actual WorkerRef.execution carries a strict bounded host locator (format/version, host nonce, original session and branch), never a model-selectable path. Before creating an execution, validate its encoding fits256 bytes. Saved Goal refs and exact same-group transcript ownership authorize lookup; ChildStore validates identifiers/paths. Current active children use their real supervisor. Retired terminal children use off-loop validated ChildStore records only; absent/unknown stays unknown. No offline Agent/supervisor is constructed. Store original refs, do not synthesize a new execution nonce on recovery.
- Parent notices become immutable SessionInput(origin='automatic', source='goal', original notice ID/instruction, bounded Goal ID/owner data), submitted through existing Core InputService/Orchestrator ports. Receipt compares exact scope/namespace/content; no standard prompt, separate message approval or Goal-aware ACP/observer projection.
- Goal role/skill instructions come from the Goal distribution. Remove unconditional Goal advice/summary from core prompt templates; generic skills keep the cached-prefix behavior. Rename retained human-input tracking to neutral input authority, not Goal lifecycle.

- [ ] Write RED tests against actual ClawConfig/GroupRuntime/Orchestrator: default no Goal tool/storage/import, strict invalid flag rejects; explicit opt-in uses the same GoalService; actual worker reads/reports under readonly, explicit acceptance and independent readonly review complete; default ordinary turn/status/idle never creates a reviewer.

```python
config = ClawConfig(data_dir=str(tmp_path), goals_enabled=True)
application = Orchestrator(config, stream_fn=scripted_model, data_dir=tmp_path)
application.register_group(Group(id='main', name='Main'))
await application.start()
await application.route_message(UserInput(group_id='main', content='Inspect output', channel='tui'))
host = application._runtimes['main'].goal_host
assert isinstance(host.service, GoalService)
```

Test fixtures construct these real existing runtime/input components and script model transport only; they must not replace GroupController, InputService, child factory or filesystem/report tools.

- [ ] Run `uv run pytest packages/claw/tests/test_goal_host.py packages/claw/tests/test_goal_host_recovery.py -q -n 0`; expected missing global flag/host and default Goal tool RED. Record the actual failing boundary before implementation.
- [ ] Implement host composition, actual group-scoped child reference recovery and role tools; route notices to existing Core Inbox. Explicitly test original Group owner persists after an idle reset, stored unaccepted terminal report can then be accepted, another group cannot inspect it, and a live child still blocks context rotation through generic existing ownership.

```python
submitted = await host.service.status(goal_id)
old_ref = submitted.tasks[0].attempts[-1].worker
runtime.reset_session()  # only after actual worker settlement
await application.route_message(UserInput(group_id='main', content='Inspect report', channel='tui'))
assert runtime.goal_host.owner == original_owner
assert (await runtime.goal_host.child_status(old_ref)).state == 'succeeded'
```

- [ ] Remove legacy manager attachment/registry/turn-end wrappers, touched-Goal labels, Gateway Goal loop/jobs/tick, observer-to-Goal projection and implicit watcher restoration/cleanup from active execution. Remove native Goal UI state/widgets/progress/finalization and Goal wire output; keep generic tool rendering and native todo. Do not preserve an inert compatibility host behind old method names.
- [ ] Replace obsolete legacy public-surface tests with new explicit contracts, preserving actual no-replay, exact original refs, human steering authority, readonly/MCP/extension gates and input/output receipts. Read-only historical transcript codecs remain only if generic transcript functionality genuinely needs them, never a hidden execution path.
- [ ] Run Claw/Coding/Goal/Agent focused suites plus full9, strict9 mypy/Ruff/Flow17/lock/diff; every remaining active Goal reference belongs to the optional adapter/packaged skill, not ACP/core prompt/UI. Record actual limitations and commit `refactor(claw): replace implicit Goal manager with optional shared capability`.

### Task 2: Delete retired domain, optional packaging and two-host installed evidence

**Files:** Delete inactive `core/planning/{goal_manager,goal_tracker,goal_review,goal_workspace,task_reports,notification_adapter}.py`, `core/tools/tool_goal.py`, old Claw Goal roles/skill and legacy-only DTOs/tests. Modify Claw pyproject extras and root lock; create actual installed Claw consumer/verifier; update architecture/acceptance evidence.

**Interfaces:** `goals` extra includes xdog-goal, `eyes` includes xdog-eyes with its required bounded ACP transport extra, `acp` contains the bounded ACP SDK; ordinary base Claw installation does not install optional feature packages. Existing search extra stays independent; all extra explicitly includes supported opt-ins without enabling their flags. No old data migration or fallback Goal implementation remains.

- [ ] Write tests for cold base Claw without Goal/Eyes/ACP installed/imported: actual ordinary assistant chat/tool/history; absent Goal storage and no autonomous startup. Explicit feature enable without its distribution fails clearly. With an old goals.json, disabled ordinary chat remains usable; enabled Goal fails byte-preserving before any new authoritative journal is created.
- [ ] Observe RED; remove retired modules/DTOs/resources and add bounded optional metadata. Fix real import boundaries rather than hiding missing dependencies in broad fallback exceptions. Preserve existing side-effect registration for memory/skills/group tools.
- [ ] Rebuild AI/Agent/Goal/TUI/Coding/Claw and any changed optional Eyes wheels+sdists, Twine and runtime/resource source parity. Run same Goal wheel through real cold installed Coding and Claw worker/report/accept/reviewer/queued-notice lifecycles. For Claw also rotate context and reopen original terminal refs without replay. No source-path injection or fake GoalHost counts as a host verification.
- [ ] Run full9, strict9, Ruff/Flow17/lock/diff; update `docs/claw-goal-cutover-verification.md` and47-item evidence without closing unrelated UI/output obligations. Commit `refactor(claw): remove retired Goal domain and isolate optional installs`.

## Follow-through

Implement generic authorized owner-bound read-only extension views in Agent, shared generic TUI rendering and the same packaged Goal Python UI extension in both real hosts. Finish observer per-assistant-message delivery/cancel unification, whole47 acceptance audit and one fresh-context whole-branch review. Neither a successful Claw cutover nor two-host artifacts alone complete the overall goal.
