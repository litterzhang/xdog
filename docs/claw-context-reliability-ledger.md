# Claw context reliability execution ledger

Plan: docs/superpowers/plans/2026-10-20-claw-context-reliability.md
Base: 605e3a6faa8f9475bb9f0ec5bb576181a33748b5

- Initial state: main, clean checkout. No implementation changes before this goal.
- User authorization: current checkout; no repeat approvals; no commit/push/release.
- Preflight: Tasks 1/2 guard Task 3 rotation. Task 3 extends the journal file
  allowlist reused by Task 5. Task 4 must keep input/output/capability close order.
- Ruling: preference inheritance is identical for /new and automatic reset;
  /new clears conversation, not user choices. Opaque extension entries remain
  Context-owned because cloning their history references would be unsafe.
- Ruling: execute inline, retain this ledger in the checkout, do not create
  worktree/commits or change release state. Explicit user constraints override
  skill defaults requiring approval/commits/worktree.

## Progress

- Design and staged plan recorded; implementation not yet verified.

## Implementation evidence (intermediate, not final completion)

- Task 1/2 RED: context-reliability-red2.log: 18 failed, 5 passed (FIFO
  excluded after separately observing original blocking FIFO read and interrupting
  that exact pytest process). GREEN with safe no-follow/nonblocking reads:
  context-reliability-green1.log: 68 passed including related persistence/IO tests.
- Cached `allow_reset=False` bypass: context-cached-red.log: 1 failed / 1 passed;
  fixed by sharing the same worker-side current-history check on both cached paths.
- Legacy restore marking success before disk read: history-restore-red.log failed
  on the second attempt. Shared PreparedHistory loader now marks restored only
  after successful adoption; also removes duplicated transcript conversion.
- Task 3 RED: context-preferences-red.log: 14 failed / 2 passed. GREEN:
  context-preferences-green.log: 87 passed (includes journal/access/owned IO).
  Runtime application controls additionally verify selected model/reasoning,
  skills/tools and model limits across daily, idle and explicit rotation without
  model calls during selection/reset/preparation.
- Task 4 RED: group-lifecycle-red.log: 4 failed (Group had no aclose). GREEN:
  group-lifecycle-green.log: 43 passed including real retired workers, repeated
  cancellation, error propagation, fencing and Goal recovery. A further RED proved
  closing group resources did not close its Agent; group close now joins it too.
- Task 5 RED: compaction-transactions-red.log: 19 failed / 1 passed. GREEN:
  compaction-transactions-green.log: 49 passed including ordinary history journal,
  session compaction and process-exit recovery tests.
- Common staged-file transaction removes repeated create/rename/checkpoint error
  handling. It retains admission-before-journal for canceled renames and explicit
  single-writer checks; a focus run caught and repaired the ownership import
  indirection regression (context-phase-green.log: 1 failed / 121 passed;
  context-final-focus1.log: 56 passed after repair).
- Interim full Claw: 1244 passed / 2 failed, both old tests expecting /new to erase
  active-tool selection. Updated assertions to the new documented inheritance
  contract; kept empty-history and no-model checks.
- Extra real-process preference crashes and compaction failed-rollback recovery:
  context-extra-recovery.log: 45 passed. Failed rollback fences live store until
  actual reopen restores complete files; no Agent exists in store recovery tests.
- Latest smaller gates: context-restore-green.log: 37 passed; Ruff packages clean;
  strict Claw: 102 source files clean (before final full workspace gate).

All log filenames above are under /data/t.Vy5SaQ; temporary fixtures only.

## Review focus

Review all changed/new persistence and runtime files against the spec, especially
safe cached reads, no silent reset, preference type validation/atomic inheritance,
journal allowlist, cache-after-commit publication, cancellation/admission boundaries,
compaction rollback/reopen and resource close error precedence. Do not expand into
new multi-user identity/routing, a new preference database, or hardware power-loss
exactly-once guarantees.

## Independent review and fix pass

- Read-only reviewer covered every modified/new source, test and design file,
  with 76 new focus tests passing but two Important integration gaps identified:
  invalid message structure could survive availability checks and be rotated
  away; glob-only branch discovery hid catalog-known missing checkpoints.
- Reproduced both plus the unknown-existing-file spec gap with 20 failing tests
  in context-review-red.log. Fixed with a pure persisted-message shape boundary
  (custom payloads remain opaque, historical compaction/text remains supported),
  catalog+file branch discovery and explicit registered-context identity checks.
- Ruling: treat the unknown-existing-file finding as a required correctness fix,
  not cosmetic follow-up: the spec explicitly forbids unknown identities from
  becoming empty contexts. No application authorization bypass was established.
- First whole-workspace gate completed all nine paths: 7163 passed, 7 skipped,
  2 failed. Both failures were owned reset cancellation gates: moving lifecycle
  code froze imports of check/reset, bypassing the established IO boundary hooks.
  Retained dynamic access to the persistence module instead of weakening tests.
- context-review-green.log: 101 passed, including all review reproducers, actual
  timeline discovery, original reset cancellation tests, source reads and history
  transactions. Ruff clean and strict Claw 103 files clean after corrections.

### Review boundary rulings

- Single writer remains required. The context transaction compares the current
  index as a fencing check, not a general concurrent catalog merge protocol.
  Adding concurrent independent stores could lose catalog-only changes; it is
  not a supported way to operate one group.
- Direct runtime close does not promise arbitrary externally started operation
  ordering. Application shutdown owns admission/turn settlement before closing
  groups; bypassing it is not a supported embedding API.
- General corrupt-catalog recovery and hostile preference-sidecar replacement
  races remain existing boundaries, not silent new repair guarantees. This run
  does not introduce data migration or a hostile-filesystem sandbox.
- Real-process crash recovery is tested, not hardware power-loss exactly-once or
  live-provider reliability. The latter are not established by local fixtures.

No independently reviewed findings are deferred in the requested scope. A final
whole-workspace rerun is still required after this fix pass.

## Requirement-by-requirement acceptance map

| Requirement | Authoritative regression coverage |
|---|---|
| Known current/archive unavailable is not empty | `test_context_reliability.py`: missing current/archive, unknown IDs, empty valid file, directory/FIFO/permission/UTF8/JSON/message-shape damage, cached preparation and repeated legacy restore |
| Reset cannot hide unavailable history | Same file: read/automatic/explicit reset preserve original bytes and pointer; cached runtime guards both allow_reset paths |
| Known checkpoint missing is visible | Same file: catalog+file discovery, branch metadata and actual `@timeline` source read report unavailable rather than omit evidence |
| Zoned timestamps, no implicit migration | Same file: naive/malformed current and resume fields rejected byte-preserving; UTC/Z and positive/negative offsets work with daily/idle predicates |
| Preferences vs Context state | `test_context_preferences.py`: both reset kinds, explicit empty selections, old history retained, fresh history/entries/counters, reasoning remains model-bound; actual application model/reasoning/skills controls and model limits survive rotation |
| Access never relaxed / preference commit atomic | `test_context_rotation_access.py`, new preference tests: sidecar/catalog/legacy scope, fault injection, failed rollback fencing and real-process partial/committed recovery |
| Group owns lifecycle, application owns admission/order | `test_group_lifecycle.py`, `test_group_controller.py`, `test_owned_reset_io.py`, `test_retired_extensions.py`, Goal recovery tests: actual child/extension/Agent closure, repeated cancellation, preserved original owner and propagated cleanup errors |
| Compaction old recovery point precedes replacement | `test_compaction_transactions.py`, `test_compaction_recovery_store.py`, `test_owned_transcript_commit.py`, SDK compaction compatibility: checkpoint sidecar/catalog/transcript faults, replacement faults, process exit, rollback failure/reopen, ordinary commit and original recovery branch |
| No extra approval/loop/Goal policy | Existing Claw/Coding input, access, Goal/observer and extension tests in the unfiltered workspace suite; changed runtime routes only lifecycle and history preparation |
| No real-data migration or publishing | Changed-file/Git-state inspection; all persistence probes and artifacts are temporary; no version, tag, publisher, channel routing or user data changes |

Completion depends on the final full-suite result below, not this coverage map alone.

## Final verification and completion audit

All five implementation tasks and the verification task are complete against the
original scope; no requirement was replaced by a narrower passing subset.

- `XDOG_MCP_OFFICIAL_SDK_ROOT=/data/t.Vy5SaQ/assistant-core-independent-sdk.zrqhiI uv run python scripts/test.py`
  → **7185 passed, 7 skipped**, all 9 configured test paths, **206.83s**, exit 0.
  Independent pinned MCP server SDK is version 2.3.1, explicitly enabled.
  Breakdown: AI 729 (+7 skips), Agent 1952, TUI 691, Flow 813, Site 42,
  Claw 1300, Coding 1263, Eyes 291, Skills 104.
  Log: `/data/t.Vy5SaQ/context-full-workspace-final.log`.
- `uv run ruff check packages` → all checks passed, exit 0.
- `uv run mypy --strict -p xdog.ai -p xdog.agent -p xdog.tui -p xdog.flow -p xdog.site -p xdog.claw -p xdog.coding -p xdog.eyes -p xdog.skills`
  → success, **606 source files**, exit 0. Existing unused override notes remain.
- `uv run xdog-flow test packages/flow/examples/ --allow-script-stub`
  → **17 passed**, exit 0.
- `uv build --package xdog-claw --out-dir /data/t.Vy5SaQ/context-claw-dist`
  → wheel and sdist built, exit 0. Wheel inspection verified all four new modules:
  context_preferences, managed_files, transcript_validation, group_lifecycle.
  These are local verification artifacts only, not a published 2.4.3 replacement.
- `git diff --check` → clean. Branch remains `main`; HEAD remains
  `605e3a6faa8f9475bb9f0ec5bb576181a33748b5`. No commit/push/tag/version changes.
- Final audit: current/known unavailable and structurally invalid history fails
  closed before rotation; known missing checkpoints remain discoverable; zoned
  timestamp reads reject invalid originals without rewriting them; explicit choices
  and restrictions survive both reset kinds while Context state is fresh; public
  group lifecycle owns resources while Orchestrator owns application ordering;
  checkpoint/replacement use durable journaled boundaries and actual process
  recovery, with no Agent/tool execution in recovery.

### Remaining guarantee boundaries (not unfinished requested work)

Single writer/application-owned shutdown ordering remain required. There is no
new multi-tenant isolation, concurrent independent-store merge, automatic corrupt
catalog repair, hostile-filesystem sandbox, live-provider certification or hardware
power-loss exactly-once guarantee. Unknown side effects are not replayed. Existing
user data and sender routing are unchanged. All independent review findings in
scope were addressed with failing regressions followed by the green full suite.

Skip audit: `uv run pytest packages/ai/tests -q -rs` → 729 passed / 7 skipped,
exit 0. All seven are explicitly opt-in external CLI integration cases (one
Claude Code --bare case and six Codex CLI cases), not skipped Claw regressions.
Log: `/data/t.Vy5SaQ/context-ai-skips.log`.
