# Claw owned transcript IO implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline. This is required implementation work discovered by the parent final-acceptance audit, not a new/reduced goal. No per-task/subplan reviewer; the parent retains the one final fresh-context whole-branch review.

**Goal:** Make Claw's actual ordinary context/history/prompt persistence non-blocking, durable before publication and settled under the existing group owner, closing audit F-01/F-03 without replaying uncertain work.

**Architecture:** Capture immutable owner-bound data on the existing execution loop, execute ordered filesystem work in owned workers, then publish session/Agent state on that same loop after real commit settlement. Reuse TranscriptStore's existing lock/navigation journal and shared `wait_owned`/`run_owned`; do not move the Agent loop to a worker or introduce another controller/store authority.

**Tech Stack:** Python3.12+ frozen snapshots, existing asyncio workers and owned-work helpers, JSONL/JSON and existing NavigationJournal/ownership/fsync, actual scripted-model and physical IO barriers.

**Spec:** `docs/superpowers/specs/2026-10-19-claw-assistant-core-design.md` sections5/7/9/11; acceptance VERIFY-01/02, RUN-03/05, IN-01/04/05/08/10. Findings: `docs/claw-assistant-core-final-audit.md`.

## Global Constraints

- Current checkout only; user authorized autonomous inline execution and no more stage approvals. No worktree/push/merge/publish/version bump/paid model/user service, existing user data changes or cache deletion.
- Bash with `TMPDIR=/data/t.Vy5SaQ UV_CACHE_DIR=/data/t.Vy5SaQ/uv-cache TMUX_TMPDIR=/data/t.Vy5SaQ`; root is full.
- Preserve one group controller/main Agent, normal per-turn input boundaries, Agent explicit steer/follow-up, Coding batch semantics, tool/readonly/MCP/extension gates and actual retained child records with no fixed business quotas.
- No Goal enable switch, implicit task/reviewer/observer startup/cancellation, migration/dual-read/repair of unknown user formats, message approval, or automatic execution replay after unknown effects.
- Existing valid native history/catalog/access/skill/tool/reasoning/compaction data must not be silently dropped. Historical data remains read-only, never activated as another main Agent. Package-owned Goal data remains outside Core.
- Freeze input data before off-loop work; never pass a mutable Agent/Session wholesale into a worker to make the fsync test pass. Business owners stay on the existing loop; a worker owns IO only.
- Cancellation settles actual writes before owner/cursor/lease release. A failed/unknown history commit cannot trigger the existing catch/finally path to append the same partial history again. Known-not-started differs from started/unknown; no false succeeded receipts.

## Source evidence / boundaries

The private-root real ordinary probe records12/12 fsync on its event-loop thread (`claw-owner-io-probe-red.log`). `get_or_create_session` creates/resets contexts synchronously; AgentSession constructor reads saved selections/model/history; prompt rebuild reads workspace/memory; run_turn calls synchronous `_persist_turn` and failure/cancel fallback `_persist`. `append_turn` lacks actual transcript fsync while increment_turn syncs metadata. Do not substitute the newer Inbox/Goal async stores for proof of these ordinary paths.

F-02 (present malformed active index) has its own typed/no-follow/byte-preserving regression fix. Respect native shared-index semantics: `owner_group` is only the uncatalogued historical archive recovery hint, not an authorization restriction. Group-key/row/session-file uniqueness and Controller scope provide current binding validation. Do not redo that fix or restore silent empty-index fallback. Inspect missing current-pointer vs derived catalog recovery separately; never guess a current Agent from archives.

## Review Focus

1. Cold session preparation, saved context restore and automatic reset must not block another group's event loop or publish a new owner before actual metadata IO settles.
2. Cancelled/failed history writes must not duplicate prefix records, advance a cursor early, release group exclusion early or call model/tools twice. Actual post-fsync/replace barriers must prove this.
3. History must retain channel/usage, canonical external inputs, tool pairing, extension context entries and compaction metadata; a plain snapshot overwrite must not discard hidden/archive records.
4. Prompt/bootstrap/memory and saved metadata/model reads must stay off the async host loop without moving Agent mutation or weakening inherited access on successor contexts.
5. Recovery may settle/undo known filesystem journal operations, never replay Agent/tool effects or infer a new current owner from an unknown/corrupt pointer. Missing view/catalog data is not new authority.

### Task 1: Owner-bound context preparation and prompt IO

**Files:** Create `packages/claw/src/xdog/claw/core/persistence/session_preparation.py` and `packages/claw/tests/test_owned_session_io.py`; modify `runtime/{group,session,orchestrator,gateway,goal_adapter}.py` as actual async callsites require. Preserve live generic lifetimes fixed in977eabd.

**Interfaces:** A frozen `PreparedSession` contains only detached metadata/history/selection/access/model/reasoning/extension-entry/compaction data needed by AgentSession, bound to original group/workspace/current context. No Agent, callbacks, entire Runtime/Session or second business store in the snapshot. `GroupRuntime.prepare_session(*, allow_reset: bool = True) -> AgentSession` is async and runs persistence preparation through existing owned IO; actual Agent/extension/supervisor construction and publication stay on the original loop. Factor constructor disk reads into the data loader rather than duplicate preparation engines. A cached current-session accessor must be IO-free and refuse absent/stale ownership; host Goal operations must not secretly create a second parent. Standalone synchronous fixture composition may use the same data loader explicitly, but no production async route may call a blocking convenience path.

- [ ] Write actual ordinary cold/restore/reset RED with real os.fsync/open observations on the private data root (delegate actual operations), record owner thread identity and require actual IO occurred. Gate actual persistence and prove an independent group remains responsive; no artificial fake backend/store/model loop or increased existing timeout.
- [ ] Run `uv run pytest packages/claw/tests/test_owned_session_io.py -q -n 0`. Expected RED: real observed owner-thread IO/preparation publication, not an import/setup failure. Port the existing actual probe to tracked regression, not a grep-only assertion.
- [ ] Implement frozen data load and owned async preparation; update real ordinary ingress/commands/auto reset and Goal adapter current-parent access. Agent system-prompt mutation stays on-loop; read/build detached workspace/memory/bootstrap content off-loop, then adopt only for the still-current owner. Preserve actual `_host_loop`, permissions, exact context and optional package discovery.
- [ ] Run new tests plus `test_input_runtime.py`, `test_group_context_controls.py`, `test_context_rotation_access.py`, `test_active_tool_persistence.py`, `test_goal_host.py`, `test_goal_extension_views.py`. Expected actual owner/rights/Goal binding unchanged and first-context preparation off-loop; no new Agent for notices/status.
- [ ] Full9/strict9/Ruff/Flow17/lock/diff; commit `refactor(claw): prepare owned session IO off loop` and task-done focused command. F-03 remains open until Task2; never label the whole fsync probe green by deleting durability calls.

### Task 2: Durable ordered history batch commit and cancellation settlement

**Files:** Add `packages/claw/tests/test_owned_transcript_commit.py`; modify `persistence/transcript_store.py`, `runtime/session.py`, small frozen commit models/helper under `persistence/` if needed. Reuse existing navigation journal/ownership; do not add another domain authority or import Goal.

**Interfaces:** Snapshot immutable new history/metadata and original owner/cursor on-loop. TranscriptStore performs one owned logical batch under its actual lock with current binding validation and actual history flush/fsync/namespace durability before success. Preserve complete prior canonical history/compaction/custom entries, append original independent message boundaries and channel/usage. Use existing undo journal for multi-file commit and publish cached store/session metadata only after the true commit marker settles. Return detached commit result; adopt `_persisted_count`/SessionMeta only on the owner loop, including the actual committed outcome after caller cancellation. Failed rollback leaves explicit existing recovery-required fencing, never an automatic persist retry or another model turn.

- [ ] Add actual JSONL-data durability RED (not just metadata fsync), delayed real commit and repeat-cancel tests. Observe full original history and actual fd data/commit order, not mock append/close/fsync away. Current append+increment without JSONL fsync must fail. Use the genuine current turn with tool/response and source metadata.
- [ ] Add real partial IO/metadata commit failure + successful rollback, and failed rollback/reopen barriers. Assert no early succeeded input receipt, no duplicate partial history, stable message/attempt identities, no model/tool replay, original owner/lease retained until actual settlement. Error precedence and definitely-unstarted cancellation must remain explicit.
- [ ] Implement ordered durable batch path and replace production `_persist_turn`/cancel/error fallback writes. Avoid worker mutation of Agent state and avoid using history length alone when compaction/custom messages change logical/disk offsets. No same-batch implicit retry in generic exception/finally handlers.
- [ ] Run new tests plus native/Weixin/ACP Core intake, output delivery, compaction/context message, Goal notices and original recipient tests. Expected per-message outputs unchanged, normal Claw per-turn unchanged, unknown execution not replayed.
- [ ] Full9/strict9/Ruff/Flow17/lock/diff; commit `fix(claw): settle durable history before execution receipts` and task-done focused command.

### Task 3: Complete application IO callsite/recovery audit and return to parent acceptance

**Files:** New `docs/claw-owned-session-io-verification.md`; update actual source/test callsites found by inspection and `docs/claw-assistant-core-final-audit.md` / requirements evidence only when proved. Every extra runtime fix needs its own observed bounded RED first.

**Interfaces:** Actual product startup, ordinary cold/warm/restore/reset/cancel/error, native controls and prompt metadata reads use the existing owner plus ordered off-loop IO. Read-only historical catalogs remain derived; current pointer corruption/missing/lease failures cannot select archive execution or initialize over unknown bytes. New source paths must retain strict active-index/namespace/identity/access checks, not a facade around the old blocking path.

- [ ] Inspect all production synchronous transcript/prompt/workspace/memory/config read/write callsites reached from async runtime or gateway, including startup and sidecar controls; distinguish fixture composition from real application traffic. Extend real IO barrier/heartbeat tests for each uncovered path before fixes. Do not claim every IO is safe merely because Inbox/Goal SQLite uses workers.
- [ ] Run tracked actual first-turn probe now green with real transcript data durability, independent group responsiveness, cancellation/reopen/no-repeat and strict current-index failure. Preserve all previous real physical terminal/socket tests and original deadlines.
- [ ] All9 unfiltered `scripts/test.py --jobs 2`, strict all9 namespaces, Ruff packages/verifiers, Flow17, lock/diff. Record exact current evidence and retained earlier timing/root-full limitations; commit `test(claw): verify owned session IO and recovery`.
- [ ] Return to parent `2026-10-19-claw-final-acceptance.md` Task2, keeping full47 rows/overall goal intact. Re-grade F-01/F-02/F-03 from actual current evidence and finish all47 audit, then the one whole-branch review. Parent Task3 rebuilds all affected final artifacts (include Site, all9 if simpler), Twine/full source parity and actual Goal-only/both-host/ACP/native/output consumers **after the last fix**. Do not reuse earlier cold snapshots or mark overall goal complete here.
