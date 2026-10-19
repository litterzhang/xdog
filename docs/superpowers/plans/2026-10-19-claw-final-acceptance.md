# Claw assistant-core final acceptance implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline, task-by-task. One fresh-context whole-branch review only after the implementation and requirement audit; no per-task reviewer.

**Goal:** Remove inert legacy group queue controls, align current product documentation, and prove the full47-item assistant-core contract before claiming completion.

**Architecture:** Claw's durable ordinary input/controller is distinct from Agent's identified explicit steer/follow-up queues. Optional Goal owns business state; shared Python UI owns only derived display. Do not introduce another execution owner, policy switch or compatibility path while deleting obsolete declarations.

**Tech Stack:** Python3.12+ frozen dataclasses/async runtime, existing uv workspace, pytest, Ruff/mypy, wheel/sdist consumers and generic extension views.

**Spec:** `docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md`; authoritative acceptance list `docs/claw-assistant-core-requirements.json`.

## Global Constraints

- User already approved autonomous inline implementation; no further stage approval. Current checkout/feature branch only. No new worktree/push/merge/publish/version bump/paid provider/user service or existing user data changes.
- Use Bash with `TMPDIR=/data/t.Vy5SaQ UV_CACHE_DIR=/data/t.Vy5SaQ/uv-cache TMUX_TMPDIR=/data/t.Vy5SaQ`; root is full. Never delete user caches to make room.
- No Goal enable switch, old-format migration, dual read, fake execution/backend/store, fixed child runtime/token/concurrency/record business quotas, or weakened tool/readonly/MCP/extension gates.
- Preserve Agent's own QueueMode and explicit steer/follow-up/checkpoints/receipts, ordinary durable Intake boundaries and Claw's current per-turn semantics. Coding batching must not regress.
- Source stop/notification stop/input withdrawal/worker stop remain independent. Unknown side effects never automatically replay. No message approval.
- A test count or prior milestone is not proof of full acceptance. Inspect source and actual artifacts for every requirement and named gate. Keep the overall goal active until all47 plus final review and current regression/artifact evidence hold.

## Current evidence and scope

Observer output plan Tasks1/2 supplied generic frozen route/Core outputv2 and observerv3 mirror, deleting aggregate sender. Task3 rebuilt `/data/observer-message-dist.LN5URO`, all14 artifacts/parity and external ACP5/Goal-only/both-host same UI proofs. That is a snapshot, not final acceptance of later cleanup. Previous default4 timing observations and root-full fixture fixes remain recorded.

Read-only search found four unused fields in Claw `GroupConfig`: `queue_mode`, `debounce_ms`, `max_concurrent`, `trigger_pattern`; only the first two have test callsites. They have no runtime consumers, unlike `max_queued_messages` (real backpressure), daily/idle reset settings and global `max_concurrent_agents` (real admission semaphore). Claw's enum is separate from Agent's actual QueueMode. Active site docs still advertise retired collect/steer-backlog/debounce behavior and wrongly claim unfinished Goals suppress automatic resets. Correct these against source, not by restoring feature coupling.

## Review Focus

1. Removing Claw queue mode must not remove Agent explicit steer/follow-up, identify acknowledgments, checkpoint consumption or native controls: use actual busy input and control tests.
2. Cleanup must preserve bounded admission/backpressure, real global semaphore, reset policy, same group single owner and cross-group isolation: retain actual controller/config tests and inspect source.
3. Current docs must describe existing transport/storage/Goal lifetime, not old implicit reviewer/Goal reset/native-widget behavior: inspect current README/site/AGENTS and classify historical milestones explicitly.
4. Fresh installed hosts must have no source import path and must exercise real tool/lease/worker/view ownership, not only discover a skill or fake an adapter: use all existing cold consumers after final runtime edits.
5. Final review must cover entire branch from actual main merge base, not only final cleanup diff; incorporate original spec, all47 audit and recorded rulings/timing limits. No overall completion before every Important/Critical finding is resolved with evidence.

### Task 1: Remove inert group configuration and current misleading documentation

**Files:** Modify `packages/claw/src/xdog/claw/core/types.py`, actual Claw test callsites (`test_input_runtime.py`, `test_orchestrator.py`, `test_acp_backend.py`, `test_eyes_process_recovery.py`, `eyes_helpers.py`, `test_eyes_manager.py`); add `packages/claw/tests/test_group_config_contract.py`; update `packages/site/src/xdog/site/content/claw.py`, `pages/claw/{reference,design}.md` and current README/AGENTS as needed.

**Interfaces:** GroupConfig retains `daily_reset_hour`, `idle_reset_seconds`, `max_queued_messages` with original defaults; removed kwargs raise normal TypeError, no aliases. No Claw QueueMode symbol; `xdog.agent.QueueMode` remains unchanged. Ordinary input remains Inbox work; explicit steer/follow-up still call the existing Agent-owned input API. Historical plan/milestone files are not rewritten to pretend the old architecture never existed.

- [ ] Write parameterized constructor tests for four removed kwargs and exact retained defaults; assert Agent enum config/modes still construct. Run targeted new contract tests. Expected RED: obsolete kwargs are still accepted, not a missing import/setup error.
- [ ] Delete enum/unused members, adapt tests by removing obsolete constructor setup only. Keep all original actual ordinary-busy/identified-steer/backpressure/cancel/ACP/source scenarios, deadlines and positive assertions.
- [ ] Run `uv run pytest packages/claw/tests/test_group_config_contract.py packages/claw/tests/test_input_runtime.py packages/claw/tests/test_orchestrator.py packages/claw/tests/test_acp_backend.py packages/claw/tests/test_eyes_process_recovery.py packages/claw/tests/test_eyes_manager.py packages/agent/tests/test_identified_inputs.py packages/agent/tests/test_input_handled.py -q -n 0`. Expected all pass, no implicit ordinary steering, no lost input and explicit Agent input consumption intact.
- [ ] Update current site/reference/features to ordinary durable Inbox and explicit Agent input controls. Correct reset/Goal storage claims only against actual current sources; do not invent future functionality or change Eyes detector debounce (different live feature).
- [ ] Run nine-package unfiltered `scripts/test.py --jobs 2`, strict9/Ruff/Flow17/lock/diff. Commit `refactor(claw): remove inert group queue configuration` and task-done focused command.

### Task 2: Requirement-by-requirement audit and one whole-branch review

**Files:** Create `docs/claw-assistant-core-final-audit.md`; update `docs/claw-assistant-core-requirements.json` and evidence documents only where current proofs justify it. Any discovered implementation fix needs its own bounded failing regression before runtime edit, with a ledgered ruling.

**Interfaces:** Each47 entry must name actual source locations, concrete runtime tests or external consumer outputs, limitations and status. Compute JSON passed/remaining counts from item statuses. Verify every referenced artifact exists, is from the current applicable runtime, and covers the claimed boundary; historical reports are not current proof. VERIFY-06 remains pending until final review/fixes/gates settle.

- [ ] Derive audit rows from the objective, original spec and all47 requirements plus named commands/artifacts; inspect each referenced actual source/test/receipt, not just file names or grep absence. List incomplete/weak/contradictory items explicitly and implement evidence-backed fixes instead of redefining success.
- [ ] Investigate actual retired extension owner references/resource settlement, callback lifetime, off-loop IO/lease/corrupt recovery and no-replay; use existing tests and add real barriers if uncovered. Preserve required guard fail-closed behavior while display failure is non-authoritative.
- [ ] Prepare full branch review package using actual `git merge-base main HEAD` and HEAD, spec, requirement list, audit, all prior ruling lines and review focus. Invoke requesting-code-review once with a fresh-context reviewer (default inherited model, no unauthorized model override). No per-task reviewer or duplicate implementation delegation.
- [ ] Re-grade findings; Important/Critical get one evidence-backed fix pass, each relevant RED->GREEN plus current related suites; explicitly ledger unresolved Minor issues. Repeat scope-specific inspection as needed, never claim review complete while required fixes remain.
- [ ] Update audit/JSON evidence without passing final verification prematurely. Commit `docs: audit assistant-core ownership and acceptance` with actual review/fix receipts as applicable.

### Task 3: Fresh final source and installed-artifact gates

**Files:** Current docs/acceptance JSON, artifact verifier scripts only if proof requires it. Rebuild all affected runtime/resource distributions after the last code edit, not reuse old cold snapshots.

**Interfaces:** Current source, full runtime/resource wheel+sdist parity, Twine and actual wheel-only consumers must agree. Both hosts use the same packaged Goal extension identity; Goal-only lacks installed/imported hosts/TUI. Optional-base hosts work with no Goal/Eyes/ACP dependencies and default permissions remain unchanged.

- [ ] Run all9 unfiltered, strict9 namespaces, Ruff packages/verifiers, Flow17, lock/diff; preserve original deadline/socket/terminal tests and record actual environment/jobs. Diagnose failures rather than relax/exclude them or quietly substitute a prior run.
- [ ] Build AI/Agent/Goal/TUI/Coding/Claw/Eyes wheels+sdists, Twine all14 and source-byte parity. If other runtime distributions changed, include them too. Record current artifact hashes.
- [ ] Fresh external consumers: `verify_acp_artifacts.py`, `verify_goal_artifact.py`, `verify_coding_goal_artifact.py`, `verify_claw_goal_artifact.py` with current dist; all actual source/UnixACP/Core outputs/native controls plus same UI/owner rotation/running-worker unload and real independent reviewer/notice intake. No project/test runtime injection.
- [ ] Audit all47 again against current exact source/artifacts/review results; pass only proved rows, compute counts, document no pending Required work. If any row is unproved, keep goal active and continue rather than declare a smaller success.
- [ ] Commit final verification evidence locally. Only when every objective deliverable and required review fix is achieved may the active goal be marked complete. This plan never authorizes push/merge/publish.
