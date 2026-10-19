# Standalone Goal Kernel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans, continuous native implementation; no additional approvals.

**Goal:** Implement the actual reusable xdog-goal distribution, owning its data and explicit worker/reviewer lifecycle without importing a harness.

**Architecture:** Frozen domain snapshots and an independently leased/versioned SQLite journal own all Goal truth, historical revisions, reports/reviews/artifacts and notification outbox. A GoalService drives explicit transitions using a small async GoalHost port; it never constructs an Agent or decides work from a turn-end callback. Role tools and packaged instructions expose only their bound capabilities. Claw/Coding adapters and shared UI cutover follow separately, not simulated as already integrated by this kernel plan.

**Tech Stack:** Python>=3.12/Linux, stdlib dataclasses/SQLite/hashlib, xdog-agent (including schema-neutral LeasedSqlite), pytest real persistence/process tests and deterministic host/model boundaries.

**Spec:** docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md, sections9/11/12/13.

## Global Constraints

- Current checkout; no worktree, user-data/service changes, paid provider, push/merge/publish or old-format upgrades.
- Goal distribution directly depends only on xdog-agent and stdlib; never import Claw/Coding/TUI/Eyes/ACP/Flow.
- No new Agent/tool loop, periodic active-goal model polling, fixed child lifecycle/token/model-call budget or message approval.
- Owner/workspace and operation authority are supplied by trusted host binding, never tool JSON/provenance.
- Worker report submitted is not task acceptance; ordinary turn-end/idle/tool-count is not completion.
- Only explicit request_complete starts an independent reviewer; all criteria/revision/attempt/evidence must still match before completion.
- Source/notification/worker cancellation are explicit separate actions, never implicit effects of completed status or UI unload.
- All authoritative writes settle off-loop and before publication. Unknown host effects are not automatically replayed.
- Root development includes the ninth package, but install/import does not enable its host feature or UI.

## Review Focus

1. Concurrent reports/criteria updates must reject stale versions without losing accepted history or overwriting unrelated work.
2. Caller cancellation after admitted storage/start/notify must settle truth; started/unknown work cannot be blindly respawned.
3. Forged worker identity, duplicate report revision and parent-supplied verdict cannot accept a task or finish a Goal.
4. Workspace evidence changes, symlinks/unsafe paths or stale reviewer execution must invalidate completion without script-as-verdict.
5. Old/corrupt/wrong-owner/leased storage is refused byte-preserving; optional UI and host imports cannot become kernel dependencies.

### Task 1: Ninth distribution, frozen domain and self-owned journal

**Files:** Create `packages/goal/pyproject.toml`, README/license/py.typed, `src/xdog/goal/{__init__,models,codec,store}.py`, tests `packages/goal/tests/test_store.py`; modify root pyproject/lock and CI import/type gates.

**Interfaces:** Frozen Owner(key,workspace), WorkerRef(id,execution), Report(task_id,attempt_id,revision,state,summary,evidence,blockers), Attempt(id,state,assignment,access,worker,reports), Acceptance(attempt_id,report_revision,report_digest,summary), Task(id,description,depends_on,state,attempts,acceptance), Finding(condition,status,evidence,reason), Review(id,state,criteria_revision,fingerprint,paths,worker,findings,verdict,summary), Goal(id,title,outcome,criteria,version,criteria_revision,status,tasks,review,summary,wait_reason,notifications_enabled), Notice(id,goal_id,instruction). All collections are immutable tuples; exact codecs reject extra/coerced/invalid data. Safe IDs are bounded strings; task dependencies are a DAG.

`GoalStore.open(path,Owner)` owns `xdog.goal.journal.v1` and validates owner before recovery. `get(id)`, `list()`, `history(id)`, `put(goal,expected_version,notices=(),artifacts=())`, `notices()`, `notice_started(id)`, `notice_settled(id,status,error='')`, `artifact(goal,key)` and `close()` are async. Put is compare-and-swap versioned, records immutable history and notices/artifacts in one transaction; duplicates with different content fail. Artifacts are bounded finite JSON owned by Goal, not executable files. Restart starting/running attempts/reviews become interrupted and sending notices become unknown, never automatically executed/replayed. Terminal submitted/accepted/report evidence remains intact.

- [ ] Write real Store tests: roundtrip/version history, owner isolation/second writer, stale CAS, goal+notice rollback, immutable report codec, strict status/tuple/DAG validation, actual process exit recovery, unsupported/corrupt byte retention and cancelled admitted commit settlement.

```python
store = await GoalStore.open(path, Owner('opaque-host-owner', workspace))
await store.put(goal, expected_version=0, notices=(notice,))
with pytest.raises(ValueError):
    await store.put(changed_same_version, expected_version=0)
assert (await store.get(goal.id)).version == 1
```

- [ ] Run `uv run pytest packages/goal/tests -q -n 0`; expected missing Goal distribution/modules RED. Configuration scaffolding needed for uv discovery may precede that run; domain implementation may not.
- [ ] Implement frozen models/exact codecs and own leased journal. Register ninth workspace/testpath/CI namespace and uv source, keep host extras/default flags for adapter cutover rather than forcing Goal as a Claw/Coding runtime dependency.
- [ ] Run focused Goal tests, full9 root runner, Ruff/strict9 mypy, lock/diff. Build Goal wheel/sdist and Twine; cold Goal-only import/store smoke must contain no installed host/Eyes/TUI/Flow. Commit `feat(goal): add independent goal domain and leased storage`.

### Task 2: Explicit GoalService, worker reports and notification host port

**Files:** Create `goal/host.py`, `service.py`, `worker.py`, `notifications.py`; tests `test_service.py`, `test_notifications.py`.

**Interfaces:** GoalHost exposes frozen owner, async start_worker/start_reviewer(spec), child_status(WorkerRef), cancel_child(WorkerRef), enqueue(Notice) and receipt(id); no Runtime/Session/Controller reference. Specs carry data/roles/tools instructions, never an Agent factory. Trusted OperationContext(user_authorized) is host-created, absent from model argument schema. Service implements create/revise/add_task/assign/report/accept_task/wait/pause/resume/abandon/cancel_task/cancel_notifications/reconcile/drain_notifications/status. `bound_reporter(goal,task,attempt)` supplies immutable worker report scope; parent actions do not include worker report or raw review-result submission.

- [ ] Write deterministic-host + real Store tests: explicit assignment with dependencies, persisted starting before host call, submitted report not accepted/complete, exact-attempt/revision conflict handling, acceptance of the selected digest, human-only criteria/user-pause release, separate task/notification/Goal state controls and no accidental child/source cleanup.

```python
await service.assign(goal.id, task.id, 'work', access='read-only')
await reporter.report(submitted)
assert (await service.status(goal.id)).tasks[0].state == 'submitted'
assert (await service.status(goal.id)).status != 'completed'
```

- [ ] Run focused tests; expected missing service/ports RED.
- [ ] Implement off-loop transactional state changes and own idempotent notice outbox. Host start failures after possible acceptance become interrupted/unknown, not blind second worker; callback/report submission cannot choose another owner. Notify stable IDs may recover only through host's idempotent enqueue contract; standard prompt outcome-unknown is not a resend path. No active-goal timer or background model task.
- [ ] Run Goal and full9 suites, Ruff/strict9 mypy. Commit `feat(goal): drive explicit tasks through small asynchronous host ports`.

### Task 3: Explicit independent review, role tools/resources and portable verification

**Files:** Create `evidence.py`, `review.py`, `tools.py`, `skills/goals/SKILL.md`, worker/reviewer role resources; tests `test_review.py`, `test_tools.py`; artifact verifier.

**Interfaces:** Goal-local fingerprint uses bounded no-follow regular-file evidence and typed criteria/task/report snapshot, no Eyes scanner. `request_complete` alone records a new review and starts a host reviewer; `reconcile` only reads the originally recorded child execution. Structured per-condition findings must cover ordered original criteria and actual evidence; stale criteria/attempt/ref/fingerprint cannot commit completion. Goal tools expose explicit parent operations and separately bound worker reporting; no complete-by-script or parent submit-verdict action. Skills use stable session-prefix instructions; UI is not a business prerequisite.

- [ ] Write tests proving no reviewer on ordinary work/status/idle, explicit reviewer starts once, every original condition needs evidence/reason, failed/blocked/stale/changed files cannot complete, symlink/path/capacity failure closes safely, report tools cannot cross attempts or accept/finish. Use real files plus deterministic child completion; no paid provider.

```python
await service.request_complete(goal.id, paths=('result.txt',))
workspace.joinpath('result.txt').write_text('changed during review')
await service.reconcile(goal.id)
assert (await service.status(goal.id)).status != 'completed'
```

- [ ] Run targeted tests; expected missing review/tools/evidence RED.
- [ ] Implement own file fingerprint/evidence validation and role tools/resources. No sync fsync/model loop on the event loop; derived files/views may be rebuilt from authoritative committed snapshots and cannot decide execution.
- [ ] Run full9/mypy9/Ruff/Flow examples/lock, build/inspect Goal artifacts and cold Goal-only actual lifecycle. Record exact evidence and remaining host/UI limits in `docs/goal-kernel-verification.md`; commit `feat(goal): require explicit evidence-backed independent completion`.

## Follow-through

Wire both real thin host adapters with explicit opt-in defaults, delete active Claw legacy GoalManager/Tracker/turn-end reviewer and inferred observer cleanup, implement generic owner-bound read-only extension views and same packaged Goal Python UI extension in both hosts, remove native Goal TUI. Unify report streaming with generic output ports and finish all47 acceptance items/fresh-context whole-branch review. A kernel-only fake-host test is never a substitute for two real host/installed executions or whole-goal completion.
