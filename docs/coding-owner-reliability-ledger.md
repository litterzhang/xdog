# Coding owner reliability execution ledger

Plan: docs/superpowers/plans/2026-10-20-coding-owner-reliability.md
Spec: docs/superpowers/specs/2026-10-20-coding-owner-reliability-design.md
Base: main / 99a15e6fc347e31fd44f9b08ec1def7b499e37e4

## Current configuration-scope clarification

The user's clarification supersedes earlier "do not read" wording below: Coding
supports its own configuration and generic AGENTS.md, not other harness config
conventions. This is not a security ban on reading CLAUDE.md. Historical guarded
read tests described in this ledger have been replaced by supported-source
selection and explicit ordinary-file access tests. No filename denylist exists.

## Authorization / environment

- User approved design+implementation and related code simplification. Coding
  automatically loads only its own configuration and generic AGENTS.md instructions.
  Current checkout, no worktree; previous no-repeat-approval direction retained. No auto commit/push/release is included in this goal.
- Initial Git state clean. /data has ample space; / has ~296 MiB free. Commands use
  TMPDIR=/data/t.Vy5SaQ, UV_CACHE_DIR=/data/t.Vy5SaQ/uv-cache. Do not clean user data.
- Earlier review evidence: /data/t.Vy5SaQ/coding-review-report.md and focused
  review reproducers. They describe actual failures, not fixed regressions.
- The old /data/t.Vy5SaQ/coding-review-probes.py assumed other harness configuration
  participated in discovery. Current tests use supported Coding/AGENTS conventions.

## Rulings

- Keep the existing controller/Agent runtime and executable Coding history branches;
  this is not the Claw read-only archive policy. Cost if wrong: unsupported loss of
  a legitimate Coding product feature.
- Automatic project instructions follow Coding/AGENTS conventions. Other harness
  configuration is not auto-loaded, but ordinary explicit file access is unchanged;
  there is no filename-based reading ban.
- allowed_tools is explicit initial active selection (None absent, [] no tools),
  never an authorization ceiling. Caller override > saved explicit selection >
  project > global > defaults; empty values must not broaden to all tools.
- Capacity policy is explicit pre-admission refusal with terminal-state headroom,
  not silent receipt deletion or a new archive subsystem. A full ledger requires
  an explicit new session; accepted effects must remain inspectable/reopenable.
- Project files are optional only when missing. Existing invalid/overlarge/unreadable
  automatic instruction sources fail preparation rather than silently omit rules.
- Skill process defaults requiring worktrees/commits/repeat approvals are overridden
  by user constraints. Work inline, preserve progress in this ledger.

## Preflight dependencies

- Task 2 stable writer/store identity feeds Task 3 navigation and Task 4 close.
- Task 3 original store capture feeds Task 7 history publication/fencing.
- Task 5 reserves capacity for Task 3/7 terminal transitions and errors.
- Task 6 effective configuration supplies Task 7 immutable preparation inputs.
- The original review scripts are evidence only. Port/strengthen tests at real
  consuming entrypoints; avoid changing tests merely to follow implementation.

## Progress

- Active goal created with all requested fixes and supported-configuration-convention constraint.
- Authoritative current files rechecked; compact design, staged implementation plan
  and acceptance strategy recorded. No product code changed or claimed fixed yet.

## Task 1: legacy extension activation — implemented, focused verification green

- New real Click -> main -> SDK bootstrap regression with offline provider,
  extensions=[] / explicitly selected sample, and an import marker. Only terminal
  boundaries are replaced; no model call occurs.
- RED: `/data/t.Vy5SaQ/coding-trust-red.log` — 2 failures: both selected/unselected
  extension code executed during ordinary startup, before worker activation.
- Removed main's eager `_load_extensions` call/helper. Caller scan showed no other
  production consumers of Coding's legacy loader/runner/types; removed those four
  files including the unused compatibility initializer. Kept AgentSession's actual
  shared ExtensionRuntime, all worker/MCP/ACP infrastructure and supported APIs.
- Updated the old frontend-mode test to stop mocking the deleted helper.
- GREEN: coding-trust-green.log — 45 passed (CLI trust/routing, selected modern
  provider and readonly/session-context extension tests). New selected-worker case
  initializes the real worker twice and verifies module execution exactly once.
- No implicit activation from directory presence; no new trust settings/framework.

## Task 6 prerequisite: supported project-context conventions — helper implemented

- Ruling: establish the supported configuration convention before broader suites
  so old resource-loader tests no longer treat other harness settings as automatic
  instructions. This prerequisite did not complete real prompt integration.
- Initial tests intercepted automatic other-harness config discovery. That test
  mechanism has since been replaced by supported-source selection and explicit
  ordinary-file access tests, matching the user's configuration-scope clarification.
- RED: coding-context-boundary-red.log — 6 failures / 4 passes: unsupported config discovery,
  followed instruction symlink, silently skipped oversized/directory/FIFO/invalid
  UTF8 context. Additional parent-symlink regression failed in coding-context-parent-red.log.
- Loader now only uses the three allowed names; descriptor-relative no-follow
  opens protect both `.coding` directory and file, reject nonregular sources
  without blocking on FIFOs, read at most 64KiB+1 bytes and fail explicitly on
  existing invalid/oversized data. Missing files remain optional; no parent walk.
- GREEN: coding-first-boundaries-green.log — 55 passed; subsequent exclusion/real
  extension focus coding-context-exclusion-green.log — 13 passed.
- Pending in Task 6: effective configuration, owned off-loop preparation and real
  model Context wiring; those are not yet implemented or reported complete.

## First implementation checkpoint (goal remains active)

- Full current Coding suite: `/data/t.Vy5SaQ/coding-first-boundaries-full.log`
  -> 1260 passed / 12 skipped, exit 0, 65.93s. The external pinned MCP SDK
  environment was not enabled for that invocation; those cases are separately
  exercised using the existing independent local SDK fixture.
- Ruff packages clean; current strict Coding gate recorded in
  `/data/t.Vy5SaQ/coding-first-boundaries-mypy.log`.
- New parent-directory symlink case proves checking only the final instruction
  link is insufficient. Loader now uses descriptor-relative opens with no-follow
  on the project root, `.coding` parent and final file; supported source selection retained.
- Remaining original goal scope is intact: stable writer/registry identity,
  admission-navigation settlement, strict writable snapshot identity, exhaustive
  close, ledger capacity/terminal headroom, effective config, real prompt wiring,
  owned history/preparation IO and final simplification/review are NOT complete.
- Next concrete step: Task 2, original-store-owned writer locks and actual SDK
  resume identity regression. Keep short socket discovery separate, cover all
  factory/navigation/runtime/ingress acquisition paths rather than patching one CLI.
- Separately enabled pinned independent SDK 2.3.1 for the exact two skipped MCP
  browser/cancellation modules: `coding-first-boundaries-sdk.log` -> 12 passed,
  exit 0, 6.14s. This supplements (does not replace) the 1260-case package run.
- Final first-checkpoint strict Coding: 95 source files clean; Ruff packages clean;
  `git diff --check` clean. No commit/push/version changes performed.

## Task 2: stable writers and strict snapshot adoption — implemented / green

- RED: coding-identity-red.log — 15 failures. Actual SDK + fresh Python process
  with a different TMPDIR acquired the same session; actual SDK corrupt-A/held-B
  case reached forbidden provider bootstrap; malformed metadata/list cases failed.
- `lease_directory(sessions)` anchors authoritative writer files under canonical
  `<sessions>/.owners`, with unchanged private ownership/flock/inode rules. Updated
  every SDK/factory/navigation/runtime acquisition and installed artifact probe.
- Ruling: short Linux Unix endpoints/discovery use deterministic private
  `/tmp/xdog-coding-UID/STORE_HASH`, not environment-selected TMPDIR. This root is
  transient discovery only; authoritative storage locks are separate. Deep store
  and cross-TMPDIR discovery tests cover the resulting path/namespace contract.
- Runtime's registry is explicitly composed from its endpoint namespace, not
  `session._lease.directory`. Metadata-only/offline ingress creates no session
  writer; its existing service lock remains in the deterministic ingress namespace.
- Operational boundary: stop pre-fix Coding writers before upgrading. Old binaries
  do not participate in the new store-anchored locking protocol. Do not operate
  mixed writer versions, auto-take-over their data, migrate or delete old records.
- Session reads now use no-follow/nonblocking regular-file descriptors, validate
  snapshot/root/timestamps/metadata, bind embedded ID to the requested filename,
  and distinguish genuine absence (None) from unavailable/invalid (explicit error).
  SDK rejection happens before provider/model/AgentSession construction or writes.
- Listings isolate corrupt files and retain diagnostics in `listing_errors` plus
  warnings; malformed existing data is not repaired into empty history. Full
  history parsing remains a separate load step, not implicit Agent construction.
- GREEN: coding-identity-green.log — 19 passed; owner/ACP/Inbox/artifact focus
  coding-owner-identity-green.log — 72 passed.

## Task 3: captured original admission owner — implemented / green

- RED: coding-admission-navigation-red.log — 2 failures on actual runtime navigation
  with a held original InputStore commit, including intake from an active control.
- A frozen `_SubmissionOwner` captures session/branch/generation/store/coordinator/
  lease per job. Admission, terminal persistence and emitted correlation use that
  owner, not whichever mutable store the controller later binds.
- Controls wait for already-started admissions; actual navigation fences input
  during identity/branch rebinding, and rotation joins original intake before old
  store/coordinator retirement and lease release. New exact intake is published by
  the existing attach_inbox composition, without another execution queue/loop.
- Preserved same-context intake during non-navigation controls. A new real thinking
  control test first failed (coding-control-intake-red.log); removed an overbroad
  `_context_changes` rejection in favor of actual session/branch/rotation fencing.
- GREEN: coding-admission-navigation-green.log — 41 passed; expanded shared batch,
  Inbox, SDK and navigation focus coding-admission-owner-green2.log — 68 passed.
- Expanded supported navigation coverage includes `/new`, existing-session resume,
  and restore_branch; original requests become discarded in their original ledger,
  successor stays healthy, and same-session branch navigation keeps its writer.
- Test correction, not product change: create_branch saves a checkpoint without
  activating it; used restore_branch for the navigation test. Likewise history
  roundtrips normalize string input into typed text parts, so verify literal
  preserved content rather than incorrect raw representation equality.
- coding-navigation-scopes-green.log — 5 passed, including all three navigation
  forms, late context rejection and unchanged-context control intake.

## Second checkpoint verification

- First full run: 1289 passed / 3 failed. The invalid-scope test expected the old
  None/False fallback; now checks explicit error plus unchanged original owner,
  restriction and target bytes. Two smoke tests constructed timestamps as strings
  despite SessionData's float contract; corrected fixture to numeric timestamps,
  not a production migration or looser validation of corrupted data.
- coding-phase2-contract-green.log — 39 passed after those contract corrections.
- `XDOG_MCP_OFFICIAL_SDK_ROOT=.../assistant-core-independent-sdk.zrqhiI uv run pytest packages/coding/tests -q -n 2`
  -> **1292 passed**, no skips, **67.25s**, exit 0.
  Log: `/data/t.Vy5SaQ/coding-phase2-full-final.log`.
- Ruff Coding + artifact verifier clean; strict Coding: **95 source files** clean.
- Remaining scope is NOT complete: exhaustive awaitable close (including its known
  cleanup-error/lease bug), capacity/terminal reservation, effective config, actual
  prompt context wiring, owned ordinary history/preparation IO, related presenter
  simplification, final workspace/build and independent review all remain required.
- Next task: Task 4. Port actual worker/cleanup-failure reproducer and unify the
  resource close contract; do not mistake successful normal close for failure-path
  settlement or release a callback/writer owner after merely scheduling cleanup.

## Task 4: shared actual close boundary — implemented, broad verification in progress

- Added public `AgentSession.aclose()` and cached, non-cancelable close receipt;
  `dispose()` requests that same close and returns its receipt. Controller/SDK/
  runtime share resource settlement; no second Agent/controller/consumer loop.
- `settle_cleanup` executes every actual cleanup and joins through repeated waiter
  cancellation, error-first. Resource error groups preserve original failures;
  writer release follows actual Agent/worker completion, not scheduled cleanup.
- Runtime endpoint retirement, SDK failure, CLI initial-input/print/REPL and ACP
  exits use the same close; terminal cleanup attempts remaining workers after error.
- A finite finalizer thread only joins an owner that cannot join itself; it runs
  no model or asyncio loop. Cold close never constructs a controller or reopens a
  damaged request ledger. No claim that a timeout constitutes resource settlement.
- RED: coding-close-owner-red.log (7 failures), coding-cleanup-helper-red.log,
  coding-cli-close-red.log (actual initial input setup leaked its writer).
  Focused GREEN: coding-lifecycle-entry-green.log, 66 passed.
- Additional actual-navigation RED: coding-retired-navigation-red.log showed old
  extension worker alive after successful navigation/old writer release. Retired
  child/extension/MCP owners are now retained and joined before original lease
  release. Focus coding-retired-navigation-green.log: 15 passed.
- Additional RED: coding-control-finalizer-red.log exposed double finalizer and
  abandoned producer receipt. Operation/settlement/finalizer run once each and
  every failure reaches the caller. coding-endpoint-close-red.log exposed a failed
  writer skipping peer joins/registry cleanup; endpoint close now shares one owned
  settlement task and attempts every stage.
- Before newest follow-ups strict Coding checked 96 files clean. Current full
  Coding/owned-work run is still in progress; do not report Task 4 fully accepted.

## Task 5: bounded request history — implementation in progress

- RED coding-capacity-red.log: unbounded admission and pending edits exceeded the
  reader limit. GREEN coding-capacity-green.log: 2 passed, using small capacity
  with actual encoding/reopen to exercise the exact production path economically.
- Common 64 MiB writer/reader limit. Every unfinished accepted input reserves
  worst-case encoded final status, 4096-character error and 256-byte batch ID;
  admission/edits fail before replace/ACK when their eventual completion cannot fit.
- Errors retain a bounded diagnostic plus explicit truncation marker, never drop
  accepted facts. No archive, automatic replay or silent deletion. Replacements
  fsync both file and parent directory. More durability/fault tests remain required.

## Task 6: effective configuration + actual prompt — implemented / focused green

- RED coding-config-red.log: SDK thinking option ignored, []/nonempty selection
  ignored, factory read both settings layers twice. RuntimeConfig now resolves
  one loaded SettingsManager snapshot with explicit options > saved > project >
  global. SDK explicit off stays off; `allowed_tools` is active selection only,
  null/absent falls through and [] selects none. No union, no permission grant.
- RED coding-config-saved-red.log: saved tuple was incorrectly fed to the wire
  list validator; normalized at SDK boundary. Saved explicit empty survives a
  later project change. coding-config-capacity-final.log: 11 passed.
- Actual SDK -> provider Context test found missing project markers and duplicated
  index (coding-prompt-red.log). Worker-owned immutable PromptResources now reads
  the permitted no-follow context sources and Agent's own SkillPlacement; each
  marker/index/body appears exactly once and project changes refresh next turn.
  Unsupported harness configuration is not included as automatic context; explicit
  ordinary-file reads remain supported. Existing context error policy retained.
- Removed Coding's filesystem-based duplicate skills summary/body discovery from
  prompt generation. Its remaining note is product UI policy only; Agent owns
  placement, session prefix and turn-scoped body lifecycle.
- RED coding-skill-worker-red.log: actual slash activation read skills on the
  controller thread. Extracted finite synchronous local command dispatch and
  execute it with run_owned under the controller's copied owner token. Direct
  SDK synchronous controls submitted to an existing owner also run in owned IO.
  coding-skill-worker-green.log: 40 passed (skills/prompt/navigation cases).
- Public synchronous SDK construction/control still has synchronous completion;
  async callers should call it via asyncio.to_thread. This does not add a second
  Agent loop or asynchronous service owner.

## Task 7: ordinary history and focused separation — implemented / focused green

- RED coding-history-red.log: ordinary turn mutated published history before disk
  success and committed on the model-owner thread. It also duplicated AgentEnd /
  producer-end writes. New immutable serialized HistorySnapshot captures the exact
  original data/branch/lease; finite worker commits it, then owner publishes fields.
- Removed event-listener persistence. Normal turn settles Agent, tool inventory and
  exactly one history commit before producer completion, including cancellation /
  partial executed tails. Commit errors retain live executed history and fence
  future intake/model use until explicit close/reload; no side-effect replay.
- Synchronous bootstrap/control persistence also stages before publication. Managed
  history replacement fsyncs its parent directory. No hardware exactly-once claim.
- coding-history-green.log: 9 passed, including actual two-turn lifecycle/usage/
  skills and actual Context; slow disk leaves the controller responsive and holds
  receipts until commit, disk failure preserves published original state.
- Separation is purposeful: lifecycle, detached prompt preparation and immutable
  history commit extracted from AgentSession; synchronous local command preparation
  extracted from async transport/extension dispatch. No generic Session framework,
  supported SDK removal, permission migration or Goal ownership change.
- Intermediate full Coding run: coding-current-full.log, 1313 passed / 3 failed.
  Two tests patched the removed event-end `_persist` seam; now inject failure at
  actual SessionManager disk commit. One lease test referenced removed SDK import;
  now patches the defining config class. These corrections retain original assertions.
- Remaining required work: full current acceptance, callback/worker audits,
  directory/cancellation edges, documentation and independent final review.

## Final boundary audit additions (before independent review acceptance)

- Actual supervisor error RED coding-supervisor-close-red.log: children drained but
  `aclose` raised before stopping/joining the real worker. Supervisor now shares
  its actual close future, always joins, preserves repeat error and attempts all
  child metadata drains. coding-supervisor-close-green.log: 46 passed.
- Actual extension persistence RED coding-entry-owner-red.log: extension callback
  published live mutable metadata before its disk commit. Extension/access saves
  now stage before publication. SessionManager serializes commits; history uses
  immutable payloads, exact owner/branch/lease/version guards and publishes under
  that same commit lock. Navigation fences original callback writes; retired
  entries are closed, not rebound onto a successor.
- Ruling: concurrent metadata revision changes invalidate an already captured
  history snapshot rather than overwrite newer facts. Fail closed and require
  explicit close/inspect/reload, with no model/tool replay. Cost: an unusual
  competing metadata write may require manual recovery instead of a silent merge.
- New branch-binding RED coding-entry-branch-red.log exposed binding successor
  callback before publishing active_branch. The successor branch is now set before
  binding its entry persistence. Regression tests exercise real staged extension
  navigation, accepted append, stale rejection and exact persisted branch.
- Unicode capacity audit: non-BMP codepoints require 12 ASCII JSON bytes each, not
  6. RED coding-unicode-reserve-red.log proved a final error could strand a queued
  input. Reserve now covers surrogate pairs; actual 64 MiB-minus-1024-byte terminal
  ledger also reopens and refuses a new unfinished input without touching old
  facts. coding-capacity-real-limit.log: 6 passed, including actual 64 MiB edge.
- Updated Coding README with ownership/upgrading, awaitable SDK shutdown, allowed
  project context, effective tool selection, bounded ledger and explicit recovery
  limits. Corrected stale Inbox approval help to automatic consumption/cancel/retry.
- New current-boundary focus coding-boundaries-final.log: 54 passed. A transient
  asyncio closed-loop child-watch diagnostic appeared; no assertion/cleanup failure.
  Keep checking latest full run rather than hiding it or treating this as final.
- Related package acceptance before the last boundary additions:
  coding-related-full.log -> 5368 passed (Agent/Coding/Claw/Skills/TUI), exit 0.
  Latest nine-package strict check: coding-all-mypy.log -> 606 files clean.
- Independent read-only whole-change reviewer dispatched; main process handles
  full workspace verification, Flow fixtures and isolated artifacts in parallel.

## Requirement-to-evidence audit (final review still pending)

| Goal requirement | Implementation / verification |
|---|---|
| 1. Explicit extension trust, no eager import | `main.py` legacy path and four caller-free modules removed; real Click/SDK import marker regression (`test_cli_extension_trust.py`) |
| 2. Stable writer authority | `lease_directory` store-owned `.owners`, separate deterministic socket discovery; genuine cross-process different-TMPDIR ownership + SDK factory/navigation/registry tests |
| 3. Original admission owner | Frozen `_SubmissionOwner`, admissions joined before controls/rotation, original store/branch/generation/lease retained; delayed commit through new/resume/restore_branch tests |
| 4. Strict saved identity and list isolation | no-follow snapshot read, requested/file/embedded ID match before provider; held-B/corrupt-A actual SDK test; malformed listing isolation/diagnostics |
| 5. Actual exhaustive close | shared public cached `aclose`/dispose receipt; actual runtime/SDK/cold worker, repeated cancellation, CLI setup/exit, Goal store lease and supervisor error tests; final reviewer auditing multi-retired/cancellation cases |
| 6. Supported real project context | three no-follow <=64KiB UTF-8 sources, explicit invalid source errors, worker PromptResources, actual SDK Context markers; other configs are ordinary explicitly readable files |
| 7. Bounded durable input facts | exact common 64MiB encoding, completion headroom incl. surrogate pairs, bounded errors/batch IDs, file+directory fsync; real near-64MiB reopen/refusal and replacement fault tests |
| 8. Effective config/off/empty tools | one SettingsManager layer snapshot into RuntimeConfig; SDK options honored, precedence and [] saved choice tests; tool selection stays separate from permissions |
| 9. Owned history and preparation IO | immutable HistorySnapshot + original identity/lease/version guard; one ordinary turn save, off-loop disk, publish after commit, partial tail fence; actual context/skill worker and slow disk tests |
| 10. Focused simplification | remove unused legacy extension machinery and producer task field; lifecycle/preparation/history separation and finite local command dispatch; no new framework/Agent loop or domain migration |

## Initial complete workspace/artifact gate (not final acceptance)

- `uv run python scripts/test.py` completed every configured path, 239.84s:
  Skills104, Flow813, Eyes291, TUI691, AI729+7skips, Agent1954, Site42, Claw1300
  passed; Coding1321 passed/1failed. Failure was one additional old regression
  waiting on inner controller.close rather than the new public session-close
  receipt; test now waits the actual returned dispose receipt. Its unchanged
  writer/compaction assertions pass: coding-close-compaction-final.log, 3 passed.
- The transient closed-loop diagnostic from an earlier focused run did not recur
  in the complete workspace run (no unhandled/unawaited-task diagnostic found).
- `uv build --all-packages` produced all nine wheels and sdists under the temporary
  artifact directory. Twine checked all18; xdog-site's pre-existing missing long
  description metadata gives warnings, not failure. Not a Coding publishing change.
- Wheel-only ACP verification succeeded in all five fresh environments (Eyes,
  Agent, Coding intake, Coding owner/batch, Claw), including installed Claw output/
  native/observer probes. Artificial DeliveryNotSent logs are deliberate failure
  injection; the verifier exits zero and records verification.json.
- Latest branch-binding fix/test was after that artifact build. Rebuild changed
  packages and repeat gates after the independent review/fix pass; do not report
  the old build or the one-failure workspace run as final green acceptance.

## Independent final review and one fix pass

- Reviewer confirmed 5 Important / 1 Minor, no Critical: snapshot/version capture
  window can lose committed metadata; same-branch replacement skips settlement;
  multi-retired cleanup fails early and finalizer releases the current writer;
  ACP double cancel loses a late constructor result; ACP disconnect endpoint
  failure skips remaining owned sessions. Actual temporary process/worker probes
  are `independent_*_review_20261020.py` under /data/t.Vy5SaQ.
- Re-grade: first five are Important and accepted. Ruling: treat the reported
  cancellation-priority Minor as Important for this fix pass: hiding a real writer
  settlement failure as ordinary cancellation gives callers the wrong safety /
  recovery signal for potentially executed effects. Cost if wrong: a small extra
  error-precedence regression/fix, no feature or ownership-policy expansion.
- Ported all six to `test_owner_review_regressions.py`; run RED before changing
  implementation. No second reviewer or new architecture is being introduced.

- Independent review RED: coding-independent-review-red.log -> 6 failed, each
  matching the reported real ownership/metadata/cancellation defect.
- Fix1: baseline captured before immutable history payload, so any concurrent
  callback commit invalidates the payload rather than lending it a new baseline.
  `test_snapshot_baseline_cannot_adopt_a_newer_committed_metadata_version` GREEN;
  durable acknowledged entry remains present and conflicting history is fenced.
- Fix2: navigation settlement considers actual resources replaced, not only scope
  strings. Re-restoring the current branch rotates generation and joins the old
  workers; next thinking control remains usable.
- Fix3: aggregate all retired groups and independently fence final writer release
  on any unresolved retired callbacks. Held real second worker prevents completion
  and writer reacquisition even after first group errors; gate release reaps all.
- Fix4: a shielded, owned canceled-construction cleanup joins the original thread
  and closes its late session through the public receipt despite repeated cancel.
- Fix5: ACP disconnect attempts ingress/request/remote/all-owned/ref cleanup;
  owned sessions close through the single session boundary including its CURRENT
  endpoint, not a separate potentially stale saved server pre-step. An endpoint
  error no longer skips the same session or the next owned session.
- Fix6: completed actual settlement errors precede deferred caller cancellation;
  wait completion is retained. No silent conversion of a disk/cleanup error into
  an ordinary cancel result.
- coding-independent-review-green.log -> **6 passed**, 7.42s; one bounded fix pass,
  all six review findings covered. No deferred code-review findings remain.
- xdog-site's existing Twine description warnings are outside this Coding change;
  recorded as pre-existing packaging metadata limitations, not a failing build.

## Final acceptance — all requested work complete

- Latest post-review/fix full configured workspace command:
  `TMPDIR=/data/t.Vy5SaQ UV_CACHE_DIR=/data/t.Vy5SaQ/uv-cache XDOG_MCP_OFFICIAL_SDK_ROOT=/data/t.Vy5SaQ/assistant-core-independent-sdk.zrqhiI uv run python scripts/test.py`
  -> **7254 passed, 7 skipped, all 9 paths completed, exit 0, 233.91s**.
  Package counts: Coding1330, Agent1954, Claw1300, TUI691, Skills104,
  AI729+7skips, Flow813, Site42, Eyes291. The seven skips are existing optional AI
  cases; Coding/MCP regressions have no skipped cases. No unawaited/unhandled task
  or closed-child-loop diagnostic in the accepted full log.
  Evidence: `/data/t.Vy5SaQ/coding-workspace-accepted.log`.
- `uv run ruff check packages scripts/verify_acp_artifacts.py` -> all checks passed
  (`coding-accepted-ruff.log`). Nine-package `mypy --strict` -> **606 source files
  clean** (`coding-accepted-mypy.log`), including skills and the shared Agent changes.
- `uv run xdog-flow test packages/flow/examples/ --allow-script-stub` -> **17 passed**
  (`coding-accepted-flow.log`). No real provider/model/service was contacted.
- Rebuilt ALL nine packages AFTER the last production fix, wheel+sdist; **18 Twine
  checks passed**. Existing xdog-site missing long-description metadata warnings
  remain, intentionally not part of this Coding refactor. Inspected Coding wheel:
  lifecycle/history/preparation boundaries included; deleted legacy loader absent.
- Repeated isolated official ACP SDK smoke against the rebuilt wheels in FIVE
  fresh environments; real independent-message owner batch, offline durable intake,
  Agent stdio, Eyes no-harness import and Claw Unix/native/output/observer recovery
  probes all exit 0. Evidence: coding-accepted-isolated.log and
  `/data/t.Vy5SaQ/xdog-acp-isolated.zpinzgnr/verification.json`.
- Independent review was executed once; all six reported findings reproduced RED,
  fixed in one bounded pass and included in the accepted full suite. No unresolved
  or deferred code-review findings. This does not claim all possible interleavings
  or hardware power-loss exactly-once behavior have been exhaustively proven.
- All ten original requirements in the matrix now have implementation and current
  acceptance evidence, including the original-owner callback/lease edge cases.
  No second Agent loop, message approval, Goal enabled switch, Goal domain storage
  migration or generic session framework was added. Existing queue/input/tool/UI
  and extension behavior is covered by the complete related suites.
- Final repository remains `main` at **99a15e6** with only this goal's uncommitted
  changes. No worktree, commit, push, version/tag or publication operation performed;
  no existing user data migrated/deleted and no CLAUDE.md content was read.
- Confirmed the seven skips explicitly with `uv run pytest packages/ai/tests -q -rs`:
  729 passed/7 skipped in 2.46s. They are opt-in external CLI integrations
  (Claude Code --bare:1, Codex CLI catalog:2, Codex CLI proxy:4), not skipped new
  ownership/ACP/MCP regressions. None of those external CLIs was launched.

## User clarification: supported configuration, not a filename ban

- Automatic discovery remains AGENTS.md + Coding's two instruction paths; no
  production discovery/tool permission changes were needed, because the loader
  already selects supported sources without a filename denylist.
- Removed global io.open/os.open interception from context tests. Other harness
  config and README fixtures are ignored as automatic instructions; actual Coding
  filesystem tools can explicitly read a temporary CLAUDE.md as an ordinary file.
- Design, plan and README now describe configuration compatibility rather than a
  file-reading prohibition. Earlier verification remains historical evidence, not
  a continuing restriction on file access or development.
- Before: context/resource focus 14 passed. After: **17 passed**, 0.35s, including
  explicit ordinary-file read and actual model Context selection. Ruff on both
  changed test modules and git diff --check pass. No production code changed in
  this clarification, and no commit/push was performed.
