# Coding Goal Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline continuously; no further approvals, no per-task reviewer.

**Goal:** Connect the independent Goal kernel to Coding's actual ChildSupervisor and durable exact-session Inbox without weakening permission gates.

**Architecture:** An optional CodingGoalHost captures immutable session/branch/workspace/inbox namespace and wraps only existing child execution and durable enqueue. Host-only one-use admission tickets bind roles to actual child IDs; labels are not authority. Optional service startup and teardown follow existing session ownership; no second Agent/controller/CLI receiver.

**Tech Stack:** Python3.12 async/ContextVar/thread locks, existing Agent ChildSupervisor, GoalService, Coding InboxStore, real temporary filesystem tests with scripted streaming models.

**Spec:** docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md sections9/11/12/13.

## Global Constraints

- Current checkout, no worktree, user-service/data/provider access, push/merge/publish or format migration.
- Goal defaults disabled; import Coding must not import optional Goal. No tool authorization changes except actual bound reporting/context capabilities, never an arbitrary tool-name exemption.
- Reviewer is actually read-only, fresh Agent, no Goal control/report authority. Filesystem/MCP/extension policies remain enforced before and after prehooks.
- Stable content-checked enqueue only, exact session+branch, no message approval; unknown effects never blindly replay.
- No turn-end review, Goal-state-driven polling/model loop, automatic observer/worker cancellation or TUI dependency.
- Completion of this plan is not whole-goal completion: actual Claw cutover, same extension UI and47-item audit remain separate required work.

## Review Focus

1. Forged labels and concurrent ordinary starts cannot obtain a Goal role: task1 ticket/ordinary-child tests.
2. Extension prehooks swapping a role call to a mutating/other tool cannot gain permission: task1 capability identity/rewritten-args tests.
3. Lost intake ACK and branch movement retain original owner/content and cannot enqueue into a successor: task1 real Inbox tests.
4. Session lifecycle must settle actual resources before a controller loop/lease disappears: task2 SDK/teardown/navigation tests.
5. Installation/import or UI absence cannot start autonomous work: task2 opt-in and headless tests.

### Task 1: Actual Coding host and immutable role admission

**Files:** Create `packages/coding/src/xdog/coding/core/goal_adapter.py`, `packages/coding/tests/test_goal_adapter.py`; modify `core/child_agents.py`, Agent `supervision.py`; add Agent subscription tests.

**Interfaces:** `CodingGoalHost.open(session)` is explicit trusted startup on an owning async loop, exposes `.service`, `.tool`, `.owner`, `.aclose()`, implements GoalHost. `capture(spec)` consumes one host-only ticket, `role_tools(spec)` durably binds WorkerRef before returning bound tools, `role_call(name, actual_tools)` verifies the actual registered object, not name. Lifecycle changes subscribe via independent ChildSupervisor.subscribe callback unaffected by UI publish replacement. Notifications persist through existing InboxStore.enqueue(expected_branch_id=...) and verify canonical records for ACK-loss receipts.

- [ ] Write failing tests exercising actual AgentSession/ChildSupervisor: worker reads and reports with default ask/read-only; explicit acceptance and independent reviewer completion; forged label/ordinary child cannot report; unauthorized writes blocked under reviewer/read-only and inherit ask/deny; exact report revision and original context; owner branch movement and inbox conflict/duplicates.

```python
host = await CodingGoalHost.open(session)
goal = await host.service.create('Output', 'Inspect file', ('result exists',), context=OperationContext(True))
goal = await host.service.add_task(goal.id, 'Inspect actual output')
goal = await host.service.assign(goal.id, goal.tasks[0].id, 'Read and report')
await session.children.supervisor.control('wait', goal.tasks[0].attempts[-1].worker.id)
assert (await host.service.status(goal.id)).tasks[0].state == 'submitted'
```

- [ ] Run `uv run pytest packages/coding/tests/test_goal_adapter.py packages/agent/tests/test_supervision_subscriptions.py -q -n 0`; expected missing adapter/subscription RED, not provider/network failure.
- [ ] Implement immutable host data binding, one-use task-local admission, actual factory role tools with cross-loop bridges, identity-scoped report/context permission treatment, event-driven settlement/notice recovery. No new Agent loop or Goal-name exemption in general permission manager.
- [ ] Run focused plus Coding/Agent/Goal suites, full9 runner, strict9 mypy/Ruff/lock/diff. Record remaining auto-lifecycle work and commit `feat(coding): adapt Goal to owned children and durable session intake`.

### Task 2: Opt-in owned lifecycle and installed Coding evidence

**Files:** Modify Coding config/settings/AgentSession/SDK lifecycle and pyproject goals extra; expand adapter tests and artifact verifier; documentation.

**Interfaces:** global trusted strict boolean `goals_enabled=False`, optional `goals` extra; lazy initialize on actual owned execution, not import/construction. The existing controller remains the sole owner while optional resources need its callback loop. Navigation retires exact capability resources and removes stale tools without transferring Goals. Shutdown joins children/host ops/store before loop/lease release; disable/unload UI never closes business service.

- [ ] Write failing actual session tests: disabled/no optional import/storage; invalid flag no coercion; enabled tool/skill after first owned execution; durable notices consumed without approval; SDK callback survives parent turn settlement; branch navigation does not move old Goals; close/reopen preserves interrupted/no replay; actual default ask denies mutation.

```python
session = create_session_with_global_goals_enabled(tmp_path)
await session.prompt('inspect')
assert session._sdk_controller is not None
assert not session.children.supervisor.active
# Navigation is allowed for idle real resources even with unfinished stored Goals.
```

- [ ] Observe RED then implement smallest integration, global opt-in only, Goal resources via session-scoped skill/context path. Bind user OperationContext only to actual consumed human input, never external label/provenance.
- [ ] Run full9, strict9, Ruff/Flow17/lock/diff; rebuild affected wheels/sdists and Twine/source parity; execute real Coding adapter lifecycle from a fresh wheel-only environment (no source injection), with model transport scripted and actual host execution/storage/permissions. Record exact evidence; commit `feat(coding): own optional Goal lifecycle in the session runtime`.

## Follow-through

Actual Claw adapter and removal of its legacy Goal authority/turn-end reviewer, then generic authorized views + same Goal Python UI extension, native UI removal, observer per-message report integration, final full47 audit and one fresh-context whole-branch review remain mandatory.
