# Coding owner reliability implementation plan

> **For agentic workers:** Execute inline with superpowers:executing-plans and
> test-driven-development. User waived repeat approval gates. No new worktree or
> automatic commits/push are authorized by this goal.

**Goal:** Repair Coding's trust, session ownership, lifecycle, context preparation,
configuration and persistence boundaries while reducing duplicate policy paths.

**Architecture:** Retain SessionController -> AgentSession -> xdog.agent.Agent.
Consolidate existing ownership/lifecycle; use detached worker IO with owner-fenced
publication. Keep native and protocol adapters thin, not new execution authorities.

**Tech Stack:** Existing Python 3.12+ uv workspace, asyncio/thread-owned IO,
frozen dataclasses, private POSIX flock/Unix endpoints, existing Agent extensions.

**Spec:** docs/superpowers/specs/2026-10-20-coding-owner-reliability-design.md

## Global constraints

- Automatic context supports AGENTS.md and the two .coding instruction paths,
  nonrecursive, 64 KiB each, no symlink following. Other harness configuration
  files are not automatic sources; explicit file reads use normal permissions.
- Preserve executable Coding branches, native batch boundaries, message automatic
  consumption and existing access/permission/capability storage boundaries.
- Current checkout only; no new loop/framework, release/version/tag/push or
  real-user-data cleanup/migration. Existing independent review probes are under
  /data/t.Vy5SaQ; use the current isolated tests for the supported configuration convention.
- RED before behavior changes; refactor under green coverage. Record rulings and
  original-owner/cancellation proof, not just passing test counts.

## Review focus

1. Different TMPDIR and long/symlinked store paths must retain one storage writer.
2. Input admission suspended at disk commit while navigation/cancellation/shutdown
   occurs must settle only the captured old store and lease, without deadlock.
3. Cleanup failures and canceled waiters must not release a writer before actual
   extension/Goal/MCP/child work and callbacks settle.
4. Persisted corruption and nearly-full request ledgers must fail before writable
   adoption/ACK, while accepted requests still settle and old facts stay readable.
5. Explicit off, empty tool selections and ignored project files must stay distinct
   from defaults; no project instruction or extension metadata grants permissions.

## Task 1: Remove legacy extension activation

Files: main.py, core/extensions/{loader,runner,types,__init__}.py after caller scan;
tests/test_no_coding_rpc.py and a new CLI trust regression file.

- [x] Write actual CLI bootstrap regression with extensions=[] and an unselected
  temp module whose import writes a marker. Stub only terminal/provider boundaries.
- [x] RED: marker is created by current startup; no real provider/services involved.
- [x] Remove legacy startup invocation; remove confirmed unused loader/runner/types
  and adjust tests of retired behavior rather than recreating a compatibility path.
- [x] GREEN: new regression + extension provider/trust/CLI startup tests; confirm
  explicitly selected modern workers still initialize lazily exactly once.

## Task 2: Stable writer identity and strict snapshot adoption

Files: core/sessions/ownership.py and all lease/registry callsites; session_manager.py,
sdk.py; ownership, ingress, SDK resume/navigation and malformed-session tests.

- [x] RED: two real processes with same store/session and different TMPDIR both
  acquire; actual SDK resume A with embedded B writes B while B remains leased.
- [x] Anchor authority in stable store-owned locks; separate short endpoint identity.
  Update every owner acquisition path (factory, navigation, SDK, runtime, ingress).
- [x] Validate requested/file/embedded IDs and root types before construction/writes;
  explicit invalid/unavailable load errors; list isolation without empty-history fallback.
- [x] GREEN: cross-process lock/release, malformed A byte-preservation with held B,
  list healthy+corrupt, exact workspace/generation and Unix path capacity tests.

## Task 3: Original-owner admission/navigation settlement

Files: core/sessions/controller.py, models/store/runtime/turns as needed;
AgentSession navigation adapter and navigation/Inbox activation tests.

- [x] RED: port the supported delayed-InputStore-commit plus new_session reproducer;
  assert navigation cannot release the old lease or poison the successor.
- [x] Capture original admission persistence scope and fence/settle admissions before
  rebinding. Avoid a reservation wait cycle; never transition an old job via new store.
- [x] GREEN: new/resume/branch delayed admission, explicit cancellation, fail-before/
  after-commit and SDK/native/ACP intake with unchanged batch/Inbox semantics.

## Task 4: One awaitable lifecycle contract

Files: AgentSession and focused lifecycle module if justified; sessions/runtime.py,
main.py print/REPL, modes/interactive/session_events.py, ACP backend cleanup.

- [x] RED: actual extension worker held during close, child close fails after actual
  settlement; same writer must remain held until remaining resources settle.
- [x] Add idempotent awaitable close, settle every resource and aggregate errors;
  share SDK/runtime implementation; update actual application callsites to await it.
- [x] GREEN: repeated cancellation, close errors, setup failures, Goal callback loop
  lifetime, retired context owners, actual CLI exit and second-writer acquisition.

## Task 5: Request ledger capacity and durability

Files: sessions/store.py, models.py and relevant bounded diagnostics/status paths;
SessionManager write helper if shared durability is appropriate.

- [x] RED: reader-valid near-64MiB ledger accepts an unreopenable next request;
  terminal error/batch growth at capacity cannot strand accepted work.
- [x] Preflight exact encoded capacity with terminal settlement reservation/bounded
  errors. Refuse new admission before ACK; keep old facts, no implicit deletion.
- [x] Add directory durability boundaries for acknowledged managed replacements.
- [x] GREEN: limit edges, after-refusal reopen, pending terminal growth, failure/
  cancellation and actual process recovery with no tool replay.

## Task 6: One effective configuration and real project/skill preparation

Files: config.py, sdk.py, settings_manager.py, resource_loader.py, system_prompt.py,
AgentSession preparation; focused immutable preparation/config helpers as needed.

- [x] RED: explicit SDK thinking off ignored; configured active selection ignored;
  actual model Context lacks AGENTS/.coding markers and repeats skill index.
- [x] Verify automatic context follows Coding/AGENTS conventions rather than
  other harness configuration; explicit ordinary-file reads remain supported.
- [x] Resolve precedence once, preserve absent vs [] tools, validate invalid files,
  keep saved explicit choices and permission policy separate.
- [x] Prepare the three permitted instruction sources and skills off-loop; project
  source errors are explicit; Agent places the index and bodies exactly once.
- [x] GREEN: actual SDK/CLI prompt, project rotation, missing/overlarge/unreadable/
  link cases, supported-source selection, explicit file reads, off/empty choices and unchanged approval gates.

## Task 7: Owned ordinary history commit and focused simplification

Files: AgentSession history/prompt methods, dedicated immutable commit helper,
SessionManager persistence; InteractiveMode presenters and proven-dead modules.

- [x] RED: ordinary turn performs duplicate saves/fsync on model owner; inject disk
  delay/error/cancellation and assert responsive owner plus committed receipt order.
- [x] Commit immutable original-context snapshots off-loop once per required history
  boundary, publish only after real commit; protect uncertain executed tails.
- [x] Consolidate duplicated SDK lifecycle, remove stale inert fields/help and
  caller-free modules; extract only product presenter/operation boundaries with
  real callers, keeping policy out of generic TUI/ACP.
- [x] GREEN: two-turn history/usage/skill tests, compaction/navigation/context entries,
  terminal images/drafts and exact extension/provider/MCP behavior.

## Task 8: Full acceptance and independent review

- [x] Review every changed/new file against spec; all numbered goal requirements
  receive explicit implementation/test evidence in the ledger.
- [x] Run Ruff packages; strict nine-package mypy; Coding and related package
  tests; `uv run python scripts/test.py`; Flow example check and necessary builds.
- [x] Independent read-only review once implementation is ready; reproduce/fix
  Important findings with RED->GREEN and rerun full suite after corrections.
- [x] Verify no real data/version/tag/publish/Git changes outside this goal; document
  remaining guarantee limits, not as substitutes for required fixes.
